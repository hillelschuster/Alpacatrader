#!/usr/bin/env python3
"""Depth-lane replay, shadow and paper runtime.

One clock-ordered decision engine drives every mode. ``replay`` feeds it a stored SIP day
(minute bars plus NBBO prints) through the same injectable stream interface the live
``shadow``/``paper`` runs use, so the offline journal is the journal the live loop would
write for the same tape. The engine owns the frozen book rules (3 slots, $3,000 reserve,
15-minute cooldown after the actual exit, 3 attempts per ticker per day, highest
prediction first) while a simulated broker owns the frozen fill rules
(``price_entry_ioc``/``submission_clock``/``priced_leg``) read on the prints observed so
far. ``paper`` replaces only that broker with an Alpaca paper account and refuses to send
an order unless ``--paper`` was passed AND ``data/forward/depth_bot/ARMED`` exists.

Operational switches are this lane's OWN: ``data/forward/depth_bot/KILL`` halts new entries
(``--kill-file`` overrides it) and ``data/forward/depth_bot/ARMED`` arms orders. The retired
flush bot's ``data/KILL`` is never read or written here - it must stay put so flush_bot stays
dead.
"""
from __future__ import annotations

import argparse
import contextlib
import heapq
import json
import os
import re
import sys
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import alpha_adaptive_depth_sizing as research  # noqa: E402
import depth_bot_features  # noqa: E402
from alpha_quote_aware_frequency import (  # noqa: E402
    MAX_AGE_S,
    SymbolQuotes,
    arrival_us,
    minute_us,
    quote_day_frame,
)
from alpha_sparse_quote_service import UNIT_EPOCH, day_quote_path  # noqa: E402

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

DATA_ROOT = research.DATA_ROOT
BOT_ROOT = DATA_ROOT / "forward" / "depth_bot"
JOURNAL_ROOT = BOT_ROOT / "journal"
ARMED_PATH = BOT_ROOT / "ARMED"
KILL_PATH = BOT_ROOT / "KILL"  # data/forward/depth_bot/KILL, NEVER the flush bot's data/KILL

ARM = research.View(0.030, 0.50)
LOSS_LIMIT_USD = 300.0  # PRE-REG-DEPTH-01 amendment (was 90)
LATTICE_START = 575
ENTRY_CUTOFF_MINUTE = 895  # research: the last decision lattice t with an entry
FLATTEN_MINUTE = 955  # ops: hard flatten at 15:55 ET
BAR_SETTLE_US = 2_000_000  # a minute is closed only after its own bar can have arrived
MAX_ORDER_NOTIONAL = 1_100.0  # ops sanity guard; the frozen sizing never reaches it
EXIT_RETRY_MAX = 5  # a dead exit order is retried at most this many times
EXIT_RETRY_BACKOFF_US = 2_000_000  # 2 s, doubling per retry
ALERT_PATH = BOT_ROOT / "ALERT"
STATES_ROOT = BOT_ROOT / "states"

UNIVERSE_MIN_FRACTION = 0.5  # a sweep covering <50% of the declared universe is incomplete
REPAIR_LOOKBACK_MIN = 6  # window re-fetched from REST when the stream misses bars
REST_REQUESTS_PER_MINUTE = 150  # the repo measures ~171 req/min as safe
REST_SYMBOLS_PER_REQUEST = 500

RELEASE_PRIORITY = 0
INTENT_PRIORITY = 1
FLATTEN_PRIORITY = 2

OPEN_POSITION, UNFILLED, UNKNOWN = "open", "unfilled", "unknown"
TERMINAL_ORDER_STATUS = {
    "filled",
    "canceled",
    "cancelled",
    "expired",
    "rejected",
    "done_for_day",
    "stopped",
    "suspended",
    "calculated",
}


# ----- small helpers ----------------------------------------------------------
def json_default(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, (Path, date)):
        return str(value)
    raise TypeError(f"cannot encode {type(value).__name__}")


def append_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as journal:
        journal.write(json.dumps(event, sort_keys=True, default=json_default) + "\n")
        journal.flush()
        os.fsync(journal.fileno())


def read_events(path: Path) -> Iterator[dict]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as journal:
        for line in journal:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def write_alert(alert_path: Path | None, kind: str, **fields: Any) -> None:
    """One JSON line per operational alert; ``None`` keeps alerts journal-only."""
    if alert_path is None:
        return
    event: dict[str, Any] = {"ts": datetime.now(ET).isoformat(), "kind": kind}
    event.update(fields)
    append_event(Path(alert_path), event)


class TokenBucket:
    """Leaky-bucket limiter with an injectable clock/sleep so tests never wait."""

    def __init__(
        self,
        per_minute: int = REST_REQUESTS_PER_MINUTE,
        *,
        clock: Any = time.monotonic,
        sleep: Any = time.sleep,
    ):
        self.capacity = max(1, int(per_minute))
        self.rate_per_s = self.capacity / 60.0
        self.tokens = float(self.capacity)
        self.clock = clock
        self.sleep = sleep
        self._last = clock()
        self.waits = 0

    def _refill(self) -> None:
        now = self.clock()
        self.tokens = min(
            float(self.capacity), self.tokens + (now - self._last) * self.rate_per_s
        )
        self._last = now

    def acquire(self, count: int = 1) -> int:
        waited = 0
        while True:
            self._refill()
            if self.tokens >= count:
                break
            self.sleep((count - self.tokens) / self.rate_per_s)
            waited += 1
        self.tokens -= count
        return waited


class HistorySource(Protocol):
    """Read-only REST history: repairs a missed sweep, rebuilds state after a restart."""

    def bars(self, day: str, tickers: list[str], start_us: int, end_us: int) -> list[dict]: ...

    def quotes(self, day: str, tickers: list[str], start_us: int, end_us: int) -> list[dict]: ...


def chunks(items: list[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(items), max(1, int(size))):
        yield items[start : start + max(1, int(size))]


class AlpacaHistory:
    """Batched SIP REST history behind a token bucket; the only network repair path."""

    def __init__(
        self,
        key: str,
        secret: str,
        *,
        per_minute: int = REST_REQUESTS_PER_MINUTE,
        batch: int = REST_SYMBOLS_PER_REQUEST,
        bucket: TokenBucket | None = None,
    ):
        self.key = key
        self.secret = secret
        self.batch = int(batch)
        self.bucket = bucket or TokenBucket(per_minute)
        self.requests = 0
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from alpaca.data.historical import StockHistoricalDataClient

            self._client = StockHistoricalDataClient(self.key, self.secret)
        return self._client

    def bars(self, day: str, tickers: list[str], start_us: int, end_us: int) -> list[dict]:
        from alpaca.data.enums import Adjustment, DataFeed
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        rows: list[dict] = []
        for batch in chunks(sorted(set(tickers)), self.batch):
            if not batch:
                continue
            self.bucket.acquire()
            self.requests += 1
            response = self.client.get_stock_bars(
                StockBarsRequest(
                    symbol_or_symbols=batch,
                    timeframe=TimeFrame.Minute,
                    start=_utc(start_us),
                    end=_utc(end_us),
                    feed=DataFeed.SIP,
                    adjustment=Adjustment.RAW,
                )
            )
            for symbol, items in (getattr(response, "data", None) or {}).items():
                for bar in items:
                    clock = bar.timestamp.astimezone(ET)
                    rows.append(
                        {
                            "ticker": symbol,
                            "et": clock.hour * 60 + clock.minute,
                            "open": float(bar.open),
                            "high": float(bar.high),
                            "low": float(bar.low),
                            "close": float(bar.close),
                            "volume": float(bar.volume),
                        }
                    )
        return rows

    def quotes(self, day: str, tickers: list[str], start_us: int, end_us: int) -> list[dict]:
        from alpaca.data.enums import DataFeed
        from alpaca.data.requests import StockQuotesRequest

        rows: list[dict] = []
        for batch in chunks(sorted(set(tickers)), self.batch):
            if not batch:
                continue
            self.bucket.acquire()
            self.requests += 1
            response = self.client.get_stock_quotes(
                StockQuotesRequest(
                    symbol_or_symbols=batch,
                    start=_utc(start_us),
                    end=_utc(end_us),
                    feed=DataFeed.SIP,
                )
            )
            for symbol, items in (getattr(response, "data", None) or {}).items():
                for item in items:
                    rows.append(
                        {
                            "symbol": symbol,
                            "ts_utc": item.timestamp,
                            "bid_price": float(item.bid_price),
                            "ask_price": float(item.ask_price),
                            "bid_size": float(item.bid_size),
                            "ask_size": float(item.ask_size),
                            "conditions": list(item.conditions or []),
                        }
                    )
        return rows


def _utc(us: int) -> datetime:
    return datetime.fromtimestamp(int(us) / 1_000_000, tz=UTC)


def ts_us_of(value: Any) -> int:
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, datetime):
        return int(value.timestamp() * 1_000_000)
    raise TypeError(f"unsupported quote clock: {type(value).__name__}")


def et_minute_of(us: int) -> int:
    clock = datetime.fromtimestamp(int(us) / 1_000_000, tz=UTC).astimezone(ET)
    return clock.hour * 60 + clock.minute


def _et_day_of(us: int) -> str:
    return datetime.fromtimestamp(int(us) / 1_000_000, tz=UTC).astimezone(ET).date().isoformat()


def _attr(value: Any, name: str, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _order_status(value: Any) -> str:
    return str(getattr(value, "value", value)).lower()


# ----- operational controls ---------------------------------------------------
def kill_active(kill_path: Path | None = None) -> bool:
    """True when THIS bot's kill file exists (the retired flush bot keeps data/KILL)."""
    return Path(kill_path if kill_path is not None else KILL_PATH).exists()


def entry_control_allowed(kill_path: Path | None = None, exit_order: bool = False) -> bool:
    """KILL suppresses new entries immediately without disabling position exits."""
    return bool(exit_order) or not kill_active(kill_path)


def paper_orders_allowed(paper_flag: bool, armed_path: Path = ARMED_PATH) -> bool:
    return bool(paper_flag and Path(armed_path).is_file())


def entries_allowed_at_minute(minute_et: int) -> bool:
    return int(minute_et) < FLATTEN_MINUTE


def flatten_due(minute_et: int) -> bool:
    return int(minute_et) >= FLATTEN_MINUTE


@dataclass
class DailyRiskStop:
    """Daily realised-loss stop: halt NEW entries, never open positions or exits."""

    limit_usd: float = LOSS_LIMIT_USD
    realized_usd: float = 0.0
    stopped: bool = False

    @property
    def entries_allowed(self) -> bool:
        return not self.stopped

    @property
    def exits_allowed(self) -> bool:
        return True

    def record_realized(self, pnl_usd: float) -> bool:
        self.realized_usd += float(pnl_usd)
        if self.realized_usd <= -abs(self.limit_usd):
            self.stopped = True
        return self.stopped


class QuoteCache:
    """Incremental per-ticker NBBO cache with the research read semantics.

    Prints are appended into preallocated numpy buffers, so a read is O(1) (a view of the
    first ``n`` slots) and the arrays are byte-identical to what
    ``alpha_quote_aware_frequency.symbol_quotes`` would build from the same prints.
    """

    _FLOAT_FIELDS = ("_bid", "_ask", "_bid_size", "_ask_size")
    _BOOL_FIELDS = ("_regular", "_valid")

    def __init__(self, day: str):
        self.day = day
        self.size_multiplier = 100 if day < UNIT_EPOCH else 1
        self.raw: dict[str, list[dict]] = {}
        self._count: dict[str, int] = {}
        self._capacity: dict[str, int] = {}
        self._stamps: dict[str, np.ndarray] = {}
        for name in (*self._FLOAT_FIELDS, *self._BOOL_FIELDS):
            setattr(self, name, {})

    def _grow(self, symbol: str, needed: int) -> None:
        capacity = self._capacity.get(symbol, 0)
        if needed <= capacity:
            return
        new_capacity = max(64, capacity * 2, needed)
        used = self._count.get(symbol, 0)
        stamps = np.empty(new_capacity, dtype=np.int64)
        if symbol in self._stamps:
            stamps[:used] = self._stamps[symbol][:used]
        self._stamps[symbol] = stamps
        for name in self._FLOAT_FIELDS:
            store = getattr(self, name)
            buffer = np.empty(new_capacity, dtype=float)
            if symbol in store:
                buffer[:used] = store[symbol][:used]
            store[symbol] = buffer
        for name in self._BOOL_FIELDS:
            store = getattr(self, name)
            buffer = np.zeros(new_capacity, dtype=bool)
            if symbol in store:
                buffer[:used] = store[symbol][:used]
            store[symbol] = buffer
        self._capacity[symbol] = new_capacity

    def add(self, quote: dict) -> None:
        symbol = str(quote["symbol"])
        conditions = [str(c) for c in (quote.get("conditions") or [])]
        regular = bool(conditions) and all(condition == "R" for condition in conditions)
        stamp = ts_us_of(quote["ts_utc"])
        bid = float(quote.get("bid_price") or 0.0)
        ask = float(quote.get("ask_price") or 0.0)
        bid_shares = float(quote.get("bid_size") or 0.0) * self.size_multiplier
        ask_shares = float(quote.get("ask_size") or 0.0) * self.size_multiplier
        valid = (
            np.isfinite(bid)
            and np.isfinite(ask)
            and np.isfinite(bid_shares)
            and np.isfinite(ask_shares)
            and bid > 0
            and ask > 0
            and bid <= ask
        )
        index = self._count.get(symbol, 0)
        self._grow(symbol, index + 1)
        self._stamps[symbol][index] = stamp
        self._bid[symbol][index] = bid
        self._ask[symbol][index] = ask
        self._bid_size[symbol][index] = bid_shares  # already in shares
        self._ask_size[symbol][index] = ask_shares
        self._regular[symbol][index] = regular
        self._valid[symbol][index] = bool(valid)
        self._count[symbol] = index + 1
        self.raw.setdefault(symbol, []).append(
            {
                "symbol": symbol,
                "ts_utc": stamp,
                "bid_price": quote.get("bid_price"),
                "ask_price": quote.get("ask_price"),
                "bid_size": quote.get("bid_size"),
                "ask_size": quote.get("ask_size"),
                "conditions": conditions,
            }
        )

    def stream(self, ticker: str) -> SymbolQuotes | None:
        count = self._count.get(ticker, 0)
        if not count:
            return None
        return SymbolQuotes(
            ticker=ticker,
            stamps=self._stamps[ticker][:count],
            bid=self._bid[ticker][:count],
            ask=self._ask[ticker][:count],
            bid_shares=self._bid_size[ticker][:count],
            ask_shares=self._ask_size[ticker][:count],
            regular=self._regular[ticker][:count],
            valid=self._valid[ticker][:count],
        )

    def capture_frames(self) -> Iterator[tuple[str, pl.DataFrame]]:
        """Per-symbol print frames straight from the buffers (no dict round trip)."""
        for ticker in sorted(self._count):
            count = self._count[ticker]
            yield ticker, pl.DataFrame(
                {
                    "symbol": [ticker] * count,
                    "ts_utc": pl.Series(self._stamps[ticker][:count]).cast(
                        pl.Datetime("us", "UTC")
                    ),
                    "bid_price": self._bid[ticker][:count],
                    "ask_price": self._ask[ticker][:count],
                    "bid_size": self._bid_size[ticker][:count] / self.size_multiplier,
                    "ask_size": self._ask_size[ticker][:count] / self.size_multiplier,
                    "conditions": pl.Series(
                        [["R"] if flag else [] for flag in self._regular[ticker][:count]],
                        dtype=pl.List(pl.String),
                    ),
                }
            )


# ----- broker boundary --------------------------------------------------------
class Broker(Protocol):
    mode: str

    def submit_entry(
        self, ticker: str, quantity: int, limit_price: float, client_order_id: str, submit_us: int
    ) -> Any: ...

    def poll_entry(self, order: Any, now_us: int) -> dict | None: ...

    def close_entry(self, order: Any, session_end_us: int) -> dict: ...

    def submit_exit(
        self, ticker: str, quantity: int, client_order_id: str, submit_us: int
    ) -> Any: ...

    def poll_exit(self, order: Any, now_us: int) -> dict | None: ...

    def close_exit(self, order: Any, session_end_us: int) -> dict: ...


class SimBroker:
    """Deterministic broker: the research fill rules applied to the observed book.

    Settlement is only ever attempted at clocks the engine has already reached, so every
    read sees the complete prefix of prints that a research replay would read for the same
    clock, and the returned outcome is the frozen one.
    """

    mode = "sim"

    def __init__(self, day: str, quotes: QuoteCache, session_end_us: int):
        self.day = day
        self.quotes = quotes
        self.session_end_us = int(session_end_us)
        self.orders: list[dict] = []

    def submit_entry(
        self, ticker: str, quantity: int, limit_price: float, client_order_id: str, submit_us: int
    ) -> dict:
        order = {
            "ticker": ticker,
            "qty": int(quantity),
            "limit_price": float(limit_price),
            "client_order_id": client_order_id,
            "submit_us": int(submit_us),
        }
        self.orders.append({"side": "buy", **order})
        return order

    def poll_entry(self, order: dict, now_us: int) -> dict | None:
        arrive = arrival_us(int(order["submit_us"]))
        if int(now_us) < arrive:
            return None
        stream = self.quotes.stream(order["ticker"])
        if stream is None:
            return {
                "state": UNKNOWN,
                "entry_status": "unknown_entry_no_prior_quote",
                "release_us": self.session_end_us + 1,
            }
        quote, status, _clock = research.price_entry_ioc(
            stream, arrive, order["limit_price"], int(order["qty"])
        )
        if quote is None:
            if status == "no_match_at_l1":
                return {"state": UNFILLED, "entry_status": status, "release_us": arrive}
            return {
                "state": UNKNOWN,
                "entry_status": status,
                "release_us": self.session_end_us + 1,
            }
        return {
            "state": OPEN_POSITION,
            "entry_status": "conditional_ioc_fill",
            "entry_ask": float(quote["ask"]),
            "entry_bid": float(quote["bid"]),
            "entry_age_s": float(quote["age_s"]),
            "entry_quote_us": int(quote["quote_us"]),
            "filled_qty": int(order["qty"]),
            "filled_avg_price": float(quote["ask"]),
        }

    def close_entry(self, order: dict, session_end_us: int) -> dict:
        return {
            "state": UNKNOWN,
            "entry_status": "unknown_entry_execution",
            "release_us": int(session_end_us) + 1,
        }

    def submit_exit(
        self, ticker: str, quantity: int, client_order_id: str, submit_us: int
    ) -> dict:
        order = {
            "ticker": ticker,
            "qty": int(quantity),
            "client_order_id": client_order_id,
            "submit_us": int(submit_us),
        }
        self.orders.append({"side": "sell", **order})
        return order

    def poll_exit(self, order: dict, now_us: int) -> dict | None:
        arrive = arrival_us(int(order["submit_us"]))
        if int(now_us) < arrive:
            return None
        stream = self.quotes.stream(order["ticker"])
        if stream is None:
            return self._unknown_exit(order, "unknown_exit_no_valid_regular_quote")
        quote, status, clock = research.priced_leg(stream, arrive, int(now_us))
        if quote is None:
            if status.startswith("unknown_stale_but_valid_at_arrival"):
                return self._unknown_exit(order, status)
            return None  # the working market order has not printed yet
        if float(quote["bid_shares"]) < int(order["qty"]):
            return self._unknown_exit(order, "unknown_exit_partial_depth", quote, arrive)
        return {
            "state": "closed",
            "exit_status": "conditional_market_fill",
            "exit_submit_us": int(order["submit_us"]),
            "exit_arrival_us": arrive,
            "exit_bid": float(quote["bid"]),
            "exit_bid_shares": float(quote["bid_shares"]),
            "exit_age_s": float(quote["age_s"]),
            "exit_quote_us": int(quote["quote_us"]),
            "exit_priced_at_status": status,
            "actual_exit_us": int(clock),
            "realized_pnl_usd": None,
        }

    def close_exit(self, order: dict, session_end_us: int) -> dict:
        return self._unknown_exit(order, "unknown_exit_no_valid_regular_quote")

    def _unknown_exit(
        self, order: dict, status: str, quote: dict | None = None, arrive: int | None = None
    ) -> dict:
        outcome = {
            "state": UNKNOWN,
            "exit_status": status,
            "exit_submit_us": int(order["submit_us"]),
            "exit_arrival_us": (
                arrive if arrive is not None else arrival_us(int(order["submit_us"]))
            ),
            "actual_exit_us": self.session_end_us + 1,
        }
        if quote is not None:
            outcome.update(
                {
                    "exit_priced_at_status": status,
                    "exit_bid": float(quote["bid"]),
                    "exit_bid_shares": float(quote["bid_shares"]),
                    "exit_age_s": float(quote["age_s"]),
                    "exit_quote_us": int(quote["quote_us"]),
                }
            )
        return outcome


class FakePaperBroker:
    """Offline paper-broker stand-in used by the test suite (never touches a network)."""

    mode = "paper"

    def __init__(self, positions: dict[str, int] | None = None, exit_price: float | None = None):
        self.positions = dict(positions or {})
        self.submitted: list[dict] = []
        self.exit_price = exit_price

    def submit_entry(
        self, ticker: str, quantity: int, limit_price: float, client_order_id: str, submit_us: int
    ) -> dict:
        order = {
            "side": "buy",
            "ticker": ticker,
            "qty": int(quantity),
            "limit_price": float(limit_price),
            "client_order_id": client_order_id,
            "submit_us": int(submit_us),
        }
        self.submitted.append(order)
        return order

    def poll_entry(self, order: dict, now_us: int) -> dict | None:
        return {
            "state": OPEN_POSITION,
            "entry_status": "conditional_ioc_fill",
            "filled_qty": int(order["qty"]),
            "filled_avg_price": float(order["limit_price"]),
            "broker_order_id": order["client_order_id"],
            "filled_at_us": int(order["submit_us"]) + 60_000,
        }

    def close_entry(self, order: dict, session_end_us: int) -> dict:
        return {
            "state": UNKNOWN,
            "entry_status": "broker_no_fill",
            "release_us": int(session_end_us) + 1,
        }

    def submit_exit(
        self, ticker: str, quantity: int, client_order_id: str, submit_us: int
    ) -> dict:
        order = {
            "side": "sell",
            "ticker": ticker,
            "qty": int(quantity),
            "client_order_id": client_order_id,
            "submit_us": int(submit_us),
        }
        self.submitted.append(order)
        return order

    def poll_exit(self, order: dict, now_us: int) -> dict | None:
        if int(now_us) < int(order["submit_us"]) + 60_000:
            return None
        outcome = {
            "state": "closed",
            "exit_status": "conditional_market_fill",
            "exit_submit_us": int(order["submit_us"]),
            "exit_arrival_us": int(order["submit_us"]),
            "actual_exit_us": int(order["submit_us"]) + 60_000,
            "broker_order_id": order["client_order_id"],
            "filled_qty": int(order["qty"]),
        }
        if self.exit_price is not None:
            outcome["filled_avg_price"] = float(self.exit_price)
        return outcome

    def close_exit(self, order: dict, session_end_us: int) -> dict:
        return {
            "state": UNKNOWN,
            "exit_status": "exit_order_still_working",
            "exit_submit_us": int(order["submit_us"]),
            "actual_exit_us": int(session_end_us) + 1,
        }


class AlpacaPaperBroker:
    """Alpaca paper adapter: marketable LIMIT IOC entry, MARKET DAY exit, nothing else."""

    mode = "paper"

    def __init__(self, trading_client):
        self.client = trading_client

    def submit_entry(
        self, ticker: str, quantity: int, limit_price: float, client_order_id: str, submit_us: int
    ):
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest

        return self.client.submit_order(
            LimitOrderRequest(
                symbol=ticker,
                qty=int(quantity),
                side=OrderSide.BUY,
                time_in_force=TimeInForce.IOC,
                limit_price=round(float(limit_price), 2),
                client_order_id=client_order_id,
            )
        )

    def submit_exit(self, ticker: str, quantity: int, client_order_id: str, submit_us: int):
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        return self.client.submit_order(
            MarketOrderRequest(
                symbol=ticker,
                qty=int(quantity),
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
                client_order_id=client_order_id,
            )
        )

    def _refresh(self, order):
        order_id = _attr(order, "id")
        if not order_id:
            return order
        try:
            return self.client.get_order_by_id(order_id)
        except Exception:  # noqa: BLE001 - a transient read must not lose the order
            return order

    def poll_entry(self, order: Any, now_us: int) -> dict | None:
        current = self._refresh(order)
        status = _order_status(_attr(current, "status", ""))
        if status not in TERMINAL_ORDER_STATUS:
            return None
        filled = float(_attr(current, "filled_qty", 0) or 0)
        if filled <= 0:
            return {"state": UNFILLED, "entry_status": status, "release_us": int(now_us)}
        return {
            "state": OPEN_POSITION,
            "entry_status": "conditional_ioc_fill",
            "filled_qty": int(filled),
            "filled_avg_price": float(_attr(current, "filled_avg_price", 0) or 0),
            "filled_at_us": ts_us_of(_attr(current, "filled_at", None) or datetime.now(UTC)),
            "broker_order_id": str(_attr(current, "id", "")),
            "partial": filled < float(_attr(current, "qty", filled) or filled),
        }

    def close_entry(self, order: Any, session_end_us: int) -> dict:
        current = self._refresh(order)
        return {
            "state": UNKNOWN,
            "entry_status": _order_status(_attr(current, "status", "unknown")),
            "release_us": int(session_end_us) + 1,
        }

    def poll_exit(self, order: Any, now_us: int) -> dict | None:
        current = self._refresh(order)
        status = _order_status(_attr(current, "status", ""))
        if status not in TERMINAL_ORDER_STATUS:
            return None
        if status != "filled":
            # canceled / expired / rejected: the POSITION IS STILL OPEN. Retry it
            # marketable; never book it closed on a non-fill.
            return {
                "state": "retry",
                "exit_status": f"exit_{status}",
                "broker_status": status,
                "broker_order_id": str(_attr(current, "id", "")),
                "exit_submit_us": int(_attr(order, "submit_us", now_us)),
                "actual_exit_us": int(now_us),
            }
        return {
            "state": "closed",
            "exit_status": "conditional_market_fill",
            "exit_submit_us": int(_attr(order, "submit_us", now_us)),
            "exit_arrival_us": int(now_us),
            "actual_exit_us": ts_us_of(_attr(current, "filled_at", None) or datetime.now(UTC)),
            "filled_qty": int(float(_attr(current, "filled_qty", 0) or 0)),
            "filled_avg_price": float(_attr(current, "filled_avg_price", 0) or 0),
            "broker_order_id": str(_attr(current, "id", "")),
        }

    def close_exit(self, order: Any, session_end_us: int) -> dict:
        current = self._refresh(order)
        filled = float(_attr(current, "filled_qty", 0) or 0)
        if filled <= 0:
            # Unfilled at the close: the position is real and still ours.
            return {
                "state": "open",
                "exit_status": "exit_order_still_working",
                "broker_status": _order_status(_attr(current, "status", "unknown")),
                "exit_submit_us": int(_attr(order, "submit_us", session_end_us)),
                "actual_exit_us": int(session_end_us) + 1,
            }
        return {
            "state": "closed",
            "exit_status": "conditional_market_fill",
            "exit_submit_us": int(_attr(order, "submit_us", session_end_us)),
            "actual_exit_us": int(session_end_us) + 1,
            "filled_qty": int(filled),
            "filled_avg_price": float(_attr(current, "filled_avg_price", 0) or 0),
            "broker_order_id": str(_attr(current, "id", "")),
        }


# ----- engine -----------------------------------------------------------------
def empty_trade_record(state: dict, quantity: int, arm=ARM) -> dict:
    """The research trade record shape, filled in as each leg resolves."""
    limit_price = float(state["limit_price"])
    record = {
        "day": state["day"],
        "arm_key": arm.key,
        "threshold": arm.threshold,
        "participation": arm.participation,
        "ticker": state["ticker"],
        "t": int(state["t"]),
        "entry_minute": int(state["entry_minute"]),
        "intent_us": int(state["intent_us"]),
        "score": float(state["pred_60"]),
        "pred": float(state["pred_60"]),
        "limit_price": limit_price,
        "qty": int(quantity),
        "qty_capital": int(state["qty_capital"]),
        "deployed_reserved_usd": research.reserved_usd(int(quantity), limit_price),
        "intent_ask_shares": float(state["intent_ask_shares"]),
        "intent_bid_shares": float(state["intent_bid_shares"]),
        "proxy_gross": state.get("proxy_gross_60"),
        "proxy_entry_status": state.get("entry_status"),
        "entry_status": None,
        "entry_ask": None,
        "entry_bid": None,
        "entry_age_s": None,
        "entry_quote_us": None,
        "entry_arrival_us": arrival_us(int(state["intent_us"])),
        "exit_minute": min(int(state["entry_minute"]) + research.HEAD, int(state["session_end"])),
        "exit_submit_us": None,
        "exit_submit_status": None,
        "exit_arrival_us": None,
        "exit_bid": None,
        "exit_bid_shares": None,
        "exit_age_s": None,
        "exit_quote_us": None,
        "exit_status": None,
        "exit_priced_at_status": None,
        "actual_exit_us": None,
        "fill_status": None,
        "attempt_index": None,
        "positions_at_entry": None,
        "broker_order_id": None,
        "broker_fill_us": None,
        "broker_fill_price": None,  # ENTRY fill price (never overwritten by the exit)
        "broker_entry_price": None,
        "broker_exit_price": None,
        "broker_filled_qty": None,
        "partial_entry": False,
        "late_exit": False,
        "parity_excluded": False,
        "parity_excluded_reason": None,
        # The simulated arm's verdict for the SAME intent, journalled beside the broker
        # fields so PRE-REG-DEPTH-01 C(i)-(iv) is computable from the journal alone.
        "sim_entry_status": None,
        "sim_entry_ask": None,
        "sim_entry_bid": None,
        "sim_entry_age_s": None,
        "sim_entry_quote_us": None,
        "sim_exit_status": None,
        "sim_exit_bid": None,
        "sim_exit_bid_shares": None,
        "sim_exit_quote_us": None,
        "sim_fill_status": None,
        "sim_would_be_unknown": None,
        "sim_net_usd_0": None,
        "sim_net_usd_25": None,
    }
    for rung in research.RUNG_COSTS:
        record[f"net_usd_{int(rung)}"] = None
    return record


@dataclass
class Position:
    ticker: str
    qty: int
    entry_minute: int
    exit_minute: int
    reserved_usd: float
    client_order_id: str
    due_us: int
    entry_price: float | None
    record: dict = field(default_factory=dict)
    exit_order: Any = None
    exit_client_id: str | None = None
    forced: bool = False
    filled_qty: int = 0
    exit_notional: float = 0.0
    realized_usd: float = 0.0
    partials: int = 0
    retries: int = 0
    next_retry_us: int = 0
    retry_blocked: bool = False
    overdue_checked: bool = False


class DepthEngine:
    """The live decision loop: incremental features, frozen book rules, broker boundary."""

    def __init__(
        self,
        day: str,
        session_end: int,
        model,
        broker: Broker,
        journal_file: Path,
        arm=ARM,
        open_positions: dict[str, dict] | None = None,
        kill_path: Path | None = None,
        risk: DailyRiskStop | None = None,
        fresh: bool = False,
        flatten: bool = True,
        quotes: QuoteCache | None = None,
        alert_path: Path | None = None,
        history: HistorySource | None = None,
        universe: list[str] | None = None,
        states_path: Path | None = None,
        mode: str = "sim",
    ):
        self.day = day
        self.session_end = int(session_end)
        self.session_end_us = minute_us(day, int(session_end))
        self.model = model
        self.broker = broker
        self.arm = arm
        self.journal_file = Path(journal_file)
        # ``None`` means this run has no kill control (an offline replay is a what-if).
        self.kill_path = Path(kill_path) if kill_path is not None else None
        self.flatten_enabled = bool(flatten)
        self.features = depth_bot_features.IncrementalDepthFeatures(
            day, self.session_end, model
        )
        self.quotes = quotes if quotes is not None else QuoteCache(day)
        self.now_us = 0
        self.cash = float(research.BOOK)
        self.active: set[str] = set()
        self.attempts: Counter[str] = Counter()
        self.cooldown_until: dict[str, int] = {}
        self.positions: dict[str, Position] = {}
        self.pending_entries: list[dict] = []
        self.pending_intents: dict[int, list[dict]] = {}
        self.tasks: list[tuple[int, int, int, str, Any]] = []
        self._task_seq = 0
        self._bar_buffer: dict[int, list[dict]] = {}
        self._flush_at: int | None = None
        self._announce: set[str] = set()
        self.bar_rows: list[dict] = []
        self.admitted: set[str] = set()
        self.decision_ticks: list[dict] = []
        self.trades: list[dict] = []
        self.state_rows: list[dict] = []
        self.alert_path = Path(alert_path) if alert_path is not None else None
        self.history = history
        self.universe: set[str] = set(universe or ())
        self.states_path = Path(states_path) if states_path is not None else None
        self.mode = mode
        # Paper runs a second, deterministic broker over the same prints: its verdict is the
        # research answer to the same intent and is journalled beside the real fill.
        self.sim: SimBroker | None = (
            SimBroker(day, self.quotes, self.session_end_us) if mode == "paper" else None
        )
        self.sim_attempts: list[dict] = []
        self.last_et: dict[str, int] = {}
        self._accepted_fresh: int | None = None
        self.refusal: str | None = None
        self.resume_us: int | None = None
        self.repairs = 0
        self._decided: set[int] = set()
        self._guarded: set[int] = set()
        self.risk = risk or DailyRiskStop()
        self.daily: dict[str, Any] = {
            "day": day,
            "arm_key": arm.key,
            "session_end": self.session_end,
            "signals": 0,
            "intents_funded": 0,
            "attempts": 0,
            "fills": 0,
            "known_fills": 0,
            "unknown_fills": 0,
            "no_match_fills": 0,
            "deployed_reserved_usd": 0.0,
            "peak_reserved_usd": 0.0,
            "positions_peak": 0,
            "positions_mean": 0.0,
            "realized_pnl_usd": 0.0,
            "risk_stopped": False,
            "parity_excluded": 0,
            "refusal": None,
            "repairs": 0,
            "restarted": False,
            "skip_reasons": Counter(),
            "_position_samples": [],
        }
        if fresh:
            self.journal_file.parent.mkdir(parents=True, exist_ok=True)
            self.journal_file.write_text("", encoding="utf-8")
        else:
            self._recover_from_journal()
        self.roll_session()
        for ticker, saved in (open_positions or {}).items():
            self._adopt_position(ticker, saved)
        if self.flatten_enabled:
            self._schedule(
                minute_us(day, min(FLATTEN_MINUTE, self.session_end)),
                FLATTEN_PRIORITY,
                "flatten",
                None,
            )
        if self.universe:
            # The sweep check needs a declared universe: it compares the prints actually
            # observed against the names that were supposed to be swept.
            for snapshot in depth_bot_features.SNAPSHOTS:
                if LATTICE_START <= snapshot <= min(ENTRY_CUTOFF_MINUTE, self.session_end - 1):
                    self._schedule(
                        minute_us(day, snapshot), INTENT_PRIORITY, "snapshot", int(snapshot)
                    )

    # -- journal / recovery ----------------------------------------------------
    def _log(self, event: dict) -> None:
        append_event(self.journal_file, event)

    def _record_state(self, state: dict) -> None:
        """Keep the exact row that drove an intent (persisted as ``states/<day>.parquet``)."""
        if self.states_path is None:
            return
        self.state_rows.append({key: state.get(key) for key in research.STATE_COLUMNS})

    def _exclude(self, record: dict, reason: str) -> None:
        """Tag a record the simulated arm cannot be compared against."""
        record["parity_excluded"] = True
        reasons = record.get("parity_excluded_reason")
        record["parity_excluded_reason"] = f"{reasons},{reason}" if reasons else reason

    def write_states(self) -> None:
        if self.states_path is None:
            return
        self.states_path.parent.mkdir(parents=True, exist_ok=True)
        frame = (
            pl.DataFrame(self.state_rows, schema=research.STATE_TYPES)
            if self.state_rows
            else research.empty_states()
        )
        frame.write_parquet(self.states_path)

    def _alert(self, kind: str, **fields: Any) -> None:
        """Journal the alert (deterministic, so replay==shadow) and append the ALERT file."""
        event = {"event": "ALERT", "day": self.day, "kind": kind, "mode": self.mode}
        event.update(fields)
        self._log(event)
        write_alert(self.alert_path, kind, day=self.day, mode=self.mode, **fields)

    def roll_session(self) -> None:
        """Close a session a crash left open, then start today's session cleanly."""
        opened = False
        closed = False
        for event in read_events(self.journal_file):
            if event.get("event") == "session_start":
                opened, closed = True, False
            elif event.get("event") == "session_end":
                closed = True
        if opened and not closed:
            self._log(
                {
                    "event": "session_end",
                    "day": self.day,
                    "mode": self.mode,
                    "rolled_over": True,
                    "trade_count": 0,
                }
            )
            self._alert("previous_session_incomplete")
        self._log(
            {
                "event": "session_start",
                "day": self.day,
                "mode": self.mode,
                "session_end": self.session_end,
            }
        )

    def _recover_from_journal(self) -> None:
        if not self.journal_file.exists():
            return
        for event in read_events(self.journal_file):
            kind = event.get("event")
            if kind == "order_submitted" and event.get("side") == "entry":
                self.attempts[event["ticker"]] += 1
            elif kind == "position_closed":
                self.cooldown_until[event["ticker"]] = int(event.get("actual_exit_us", 0)) + (
                    research.COOLDOWN_MIN * 60_000_000
                )
                self.risk.record_realized(float(event.get("realized_pnl_usd", 0.0)))

    def _adopt_position(self, ticker: str, saved: dict) -> None:
        quantity = int(saved.get("qty", 0))
        if quantity <= 0:
            return
        entry_minute = int(saved.get("entry_minute", 0))
        clock_known = bool(saved.get("entry_clock_known", entry_minute)) and entry_minute > 0
        if not clock_known:
            # The broker has no fill clock for this position: adopt it at the restart minute
            # and mark it as a late, parity-excluded exit instead of a 01:00 skeleton.
            entry_minute = et_minute_of(self.now_us) if self.now_us else 0
        exit_minute = int(saved.get("exit_minute", 0)) or min(
            entry_minute + research.HEAD, self.session_end
        )
        position = Position(
            ticker=ticker,
            qty=quantity,
            entry_minute=entry_minute,
            exit_minute=exit_minute,
            reserved_usd=float(saved.get("reserved_usd", 0.0)),
            client_order_id=str(saved.get("client_order_id", f"adopted-{ticker}")),
            due_us=minute_us(self.day, exit_minute),
            entry_price=float(saved.get("entry_price", 0.0)) or None,
            record=dict(saved.get("record") or {}),
        )
        if not clock_known:
            position.record["late_exit"] = True
            self._exclude(position.record, "entry_clock_unknown")
        if not position.record or not position.record.get("intent_us"):
            position.record = {
                "day": self.day,
                "ticker": ticker,
                "qty": quantity,
                "entry_minute": entry_minute,
                "exit_minute": exit_minute,
                "entry_price": position.entry_price,
                "entry_ask": position.entry_price,
                "broker_fill_price": position.entry_price,
                "fill_status": None,
                "exit_bid": None,
                "actual_exit_us": None,
                "realized_pnl_usd": None,
                "late_exit": False,
                **{f"net_usd_{int(rung)}": None for rung in research.RUNG_COSTS},
            }
        position.forced = bool(saved.get("late_exit", False))
        self.positions[ticker] = position
        self.active.add(ticker)
        self.cash -= position.reserved_usd
        self._log(
            {
                "event": "reconcile_adopt",
                "day": self.day,
                "ticker": ticker,
                "qty": quantity,
                "entry_minute": entry_minute,
                "exit_minute": exit_minute,
            }
        )

    # -- scheduling ------------------------------------------------------------
    def _schedule(self, clock_us: int, priority: int, kind: str, payload: Any) -> None:
        self._task_seq += 1
        heapq.heappush(
            self.tasks, (int(clock_us), priority, self._task_seq, kind, payload)
        )

    def advance(self, now_us: int) -> None:
        if int(now_us) <= self.now_us:
            return
        self.now_us = int(now_us)
        self._close_minutes()
        self._flag_overdue_positions()
        self._poll_orders()
        self._drain_tasks()

    def _flag_overdue_positions(self) -> None:
        """Any position whose exit clock already passed is late: exit at once, and alert."""
        for position in self.positions.values():
            if position.overdue_checked:
                continue
            position.overdue_checked = True
            if int(position.due_us) > int(self.now_us) or position.forced:
                continue
            position.forced = True
            position.record["late_exit"] = True
            self._exclude(position.record, "late_exit")
            self._log(
                {
                    "event": "late_exit",
                    "day": self.day,
                    "ticker": position.ticker,
                    "qty": position.qty,
                    "due_us": int(position.due_us),
                    "now_us": int(self.now_us),
                }
            )
            self._alert(
                "late_exit",
                ticker=position.ticker,
                qty=position.qty,
                due_us=int(position.due_us),
                now_us=int(self.now_us),
            )

    # -- stream callbacks ------------------------------------------------------
    def _announce_drain(self) -> set[str]:
        added, self._announce = set(self._announce), set()
        return added

    def _sync_admissions(self) -> None:
        new = set(self.features.admissions) - self.admitted
        self.admitted |= new
        self._announce |= new

    def on_bar(self, bars: pl.DataFrame) -> set[str]:
        rows = (
            bars.select("ticker", "et", "open", "high", "low", "close", "volume")
            .sort(["et", "ticker"])
            .to_dicts()
        )
        for row in rows:
            minute = int(row["et"])
            ticker = row["ticker"]
            if ticker in self.last_et and minute <= int(self.last_et[ticker]):
                continue  # already rebuilt from history: the stream repeats it
            if (
                self.resume_us is not None
                and minute_us(self.day, minute + 1) <= self.resume_us
            ):
                continue  # this minute ended before the rebuilt state was taken
            self.advance(minute_us(self.day, minute + 1))
            self._bar_buffer.setdefault(minute, []).append(row)
            flush_at = minute_us(self.day, minute + 1) + BAR_SETTLE_US
            if self._flush_at is None or flush_at < self._flush_at:
                self._flush_at = flush_at
        return self._announce_drain()

    def on_quote(self, quote: dict) -> set[str]:
        symbol = str(quote["symbol"])
        if symbol in self.admitted:
            self.quotes.add(quote)
        self.advance(ts_us_of(quote["ts_utc"]))
        return self._announce_drain()

    # -- minute/decision pipeline ---------------------------------------------
    def _close_minutes(self) -> None:
        if self._flush_at is None or self.now_us < self._flush_at:
            return
        ready = sorted(
            minute
            for minute in self._bar_buffer
            if minute_us(self.day, minute + 1) + BAR_SETTLE_US <= self.now_us
        )
        for minute in ready:
            self._flush_minute(minute)
        self._flush_at = (
            min(minute_us(self.day, minute + 1) + BAR_SETTLE_US for minute in self._bar_buffer)
            if self._bar_buffer
            else None
        )

    def _flush_minute(self, minute: int, *, decide: bool = True) -> None:
        rows = self._bar_buffer.pop(minute, [])
        if not rows:
            return
        self.features.add_bars(pl.DataFrame(rows))
        self.bar_rows.extend(rows)
        for row in rows:
            ticker = row["ticker"]
            self.last_et[ticker] = int(row["et"])
        self._sync_admissions()
        t = minute + 1
        if not decide:
            return
        if t % 5 or not LATTICE_START <= t <= min(ENTRY_CUTOFF_MINUTE, self.session_end - 1):
            return
        if self.refusal is not None or t in self._decided:
            return
        self._decided.add(t)
        self._decide_tick(t)

    def _flush_decisions(self, t: int) -> None:
        scored = self.features.decision(t)
        # A snapshot inside decision(t) can admit names; they must be quote-tracked from
        # this minute on, not from the next flush (2025-07-31 SNGX t=630).
        self._sync_admissions()
        rows = scored.to_dicts() if scored.height else []
        signals = [row for row in rows if float(row["pred_60"]) >= self.arm.threshold]
        tick = {
            "event": "decision_tick",
            "day": self.day,
            "t": int(t),
            "candidate_count": len(rows),
            "signal_count": len(signals),
            "arm_key": self.arm.key,
        }
        self.decision_ticks.append(tick)
        self.daily["signals"] += len(signals)
        self._log(tick)
        for row in signals:
            minute_next = int(row["t"]) + 1
            self._log(
                {
                    "event": "signal",
                    "day": self.day,
                    "ticker": row["ticker"],
                    "t": int(row["t"]),
                    "intent_minute": minute_next,
                    "pred_60": float(row["pred_60"]),
                    "arm_key": self.arm.key,
                }
            )
            self.pending_intents.setdefault(minute_next, []).append(row)
            self._schedule(
                minute_us(self.day, minute_next), INTENT_PRIORITY, "intent", minute_next
            )

    # -- universe coverage -----------------------------------------------------
    def _sweep_stats(self, t: int) -> dict[str, Any]:
        fresh = sum(1 for et in self.last_et.values() if int(et) >= int(t) - 2)
        newest = max(self.last_et.values()) if self.last_et else None
        return {"fresh": fresh, "alive": len(self.last_et), "newest": newest}

    def guard_snapshot(self, t: int) -> bool:
        """Run the sweep check once per snapshot minute (scheduled or at decision time)."""
        t = int(t)
        if t in self._guarded:
            return self.refusal is None
        self._guarded.add(t)
        return self._guard_snapshot(t)

    def _guard_snapshot(self, t: int) -> bool:
        """Refuse the session when the snapshot sweep is empty, stale or collapsed."""
        stats = self._sweep_stats(t)
        reason = self._sweep_verdict(t, stats)
        if (
            reason is not None
            and self.history is not None
            and self._repair_bars(t)
        ):
            self.repairs += 1
            self.daily["repairs"] = self.repairs
            stats = self._sweep_stats(t)
            reason = self._sweep_verdict(t, stats)
        if reason is None:
            self._accepted_fresh = int(stats["fresh"])
            ratio = float(stats["alive"]) / len(self.universe) if self.universe else None
            self._log(
                {
                    "event": "snapshot_coverage",
                    "day": self.day,
                    "t": int(t),
                    "declared": len(self.universe),
                    "ratio": ratio,
                    **stats,
                }
            )
            return True
        self.refuse(reason, t=int(t), **stats)
        return False

    def _sweep_verdict(self, t: int, stats: dict[str, Any]) -> str | None:
        if not stats["alive"]:
            return "universe_empty"
        if stats["newest"] is None or int(stats["newest"]) < int(t) - 2:
            return "universe_stale"
        declared = len(self.universe)
        if declared and int(stats["alive"]) < UNIVERSE_MIN_FRACTION * declared:
            # A subscription cap or a partial feed looks exactly like a quiet tape; the
            # declared universe is the only honest denominator, so fail closed.
            return "universe_incomplete"
        return None

    def refuse(self, reason: str, **fields: Any) -> None:
        """Stop trading this session; entries are refused from here on (exits continue)."""
        if self.refusal is not None:
            return
        self.refusal = reason
        self.daily["refusal"] = reason
        self._log({"event": "universe_refused", "day": self.day, "reason": reason, **fields})
        self._alert("universe_refused", reason=reason, **fields)

    def _repair_bars(self, t: int) -> int:
        """REST fallback for minutes the stream missed; rate limited, batched."""
        start_us = minute_us(self.day, max(LATTICE_START - 5, int(t) - REPAIR_LOOKBACK_MIN))
        end_us = minute_us(self.day, int(t))
        tickers = sorted(self.universe or set(self.last_et))
        if not tickers or self.history is None:
            return 0
        try:
            rows = self.history.bars(self.day, tickers, start_us, end_us)
        except Exception as exc:  # noqa: BLE001 - a repair failure must never crash the loop
            self._alert("backfill_failed", reason=type(exc).__name__, detail=str(exc))
            return 0
        return self.merge_bars(rows)

    def merge_bars(self, rows: list[dict]) -> int:
        """Add bars the engine has not seen yet (never a duplicate or a backwards minute).

        Real tapes have gaps - a symbol simply did not trade - so only monotonicity is
        enforced; the incremental feature state accepts exactly what the research panel
        accepts, rows are never forward-filled.
        """
        accepted: dict[int, list[dict]] = {}
        added = 0
        for row in sorted(rows, key=lambda item: (int(item["et"]), item["ticker"])):
            ticker = row["ticker"]
            minute = int(row["et"])
            if ticker in self.last_et and minute <= int(self.last_et[ticker]):
                continue
            accepted.setdefault(minute, []).append(row)
            self.last_et[ticker] = minute
            added += 1
        for minute in sorted(accepted):
            batch = accepted[minute]
            self.features.add_bars(pl.DataFrame(batch))
            self.bar_rows.extend(batch)
        self._sync_admissions()
        return added

    # -- restart backfill ------------------------------------------------------
    def session_start_us(self) -> int:
        return minute_us(self.day, LATTICE_START - 5)  # 09:30

    def fetch_bars(self, start_us: int, end_us: int) -> list[dict]:
        if self.history is None:
            return []
        tickers = sorted(self.universe or set(self.last_et))
        if not tickers:
            return []
        try:
            return self.history.bars(self.day, tickers, start_us, end_us)
        except Exception as exc:  # noqa: BLE001 - a REST failure must never crash the loop
            self._alert("backfill_failed", scope="bars", reason=type(exc).__name__,
                        detail=str(exc))
            return []

    def fetch_quotes(self, start_us: int, end_us: int) -> list[dict]:
        if self.history is None or not self.admitted:
            return []
        try:
            return self.history.quotes(self.day, sorted(self.admitted), start_us, end_us)
        except Exception as exc:  # noqa: BLE001
            self._alert("backfill_failed", scope="quotes", reason=type(exc).__name__,
                        detail=str(exc))
            return []

    def bootstrap(
        self,
        bars: list[dict] | None = None,
        quotes: list[dict] | None = None,
        *,
        resume_us: int,
        journal_replay: bool = True,
    ) -> None:
        """Rebuild the incremental state from REST history before the first new decision."""
        self.resume_us = int(resume_us)
        if bars is None:
            bars = self.fetch_bars(self.session_start_us(), self.resume_us)
        self.merge_bars(bars)
        resume_minute = et_minute_of(self.resume_us)
        if journal_replay:
            for snapshot in depth_bot_features.SNAPSHOTS:
                if snapshot <= resume_minute - 1:
                    self.features.feature_frame(snapshot)
            self._sync_admissions()
        if quotes is None:
            quotes = self.fetch_quotes(self.session_start_us(), self.resume_us)
        for quote in quotes or ():
            if str(quote["symbol"]) in self.admitted:
                self.quotes.add(quote)
        self.now_us = self.resume_us
        for position in self.positions.values():
            if int(position.entry_minute) <= 0:
                # The broker had no fill clock for this position: adopt it at the restart
                # minute, flagged, instead of a 01:00 skeleton.
                position.entry_minute = et_minute_of(self.now_us)
                position.exit_minute = min(
                    position.entry_minute + research.HEAD, self.session_end
                )
                position.due_us = minute_us(self.day, position.exit_minute)
                position.record["entry_minute"] = position.entry_minute
                position.record["exit_minute"] = position.exit_minute
                position.record["late_exit"] = True
                self._exclude(position.record, "entry_clock_unknown")
            if int(position.due_us) <= self.now_us and not position.forced:
                # The exit clock already passed while the process was down.
                position.forced = True
                position.record["late_exit"] = True
                self._exclude(position.record, "late_exit")
        self.daily["restarted"] = True
        self._log(
            {
                "event": "session_resumed",
                "day": self.day,
                "resumed_at_us": int(resume_us),
                "bars": len(self.bar_rows),
                "quotes": sum(len(rows) for rows in self.quotes.raw.values()),
                "admitted": len(self.admitted),
            }
        )
        # Ticks whose decision clock is already past resume but whose intent is still in
        # the future must be produced now, exactly as the uninterrupted run produced them.
        for t in range(LATTICE_START, min(ENTRY_CUTOFF_MINUTE, self.session_end - 1) + 1, 5):
            if not (minute_us(self.day, t) <= self.resume_us < minute_us(self.day, t + 1)):
                continue  # only the tick whose window closed exactly now
            if t in self._decided:
                continue
            self._decided.add(t)
            self._decide_tick(t)

    def _decide_tick(self, t: int) -> None:
        if t in depth_bot_features.SNAPSHOTS and not self.guard_snapshot(t):
            return
        self._flush_decisions(t)

    # -- intent/funding --------------------------------------------------------
    def _state_row(self, row: dict, minute: int, intent_us: int, facts: dict) -> dict:
        return {
            **row,
            "day": self.day,
            "entry_minute": int(minute),
            "intent_us": int(intent_us),
            "session_end": self.session_end,
            # Both proxies need the entry minute's own bar, which has not closed at the
            # decision instant: record the causal truth, never the research hindsight value.
            "proxy_gross_60": None,
            "qty_capital": int(facts["qty_capital"]),
            **facts,
        }

    def _book_reason(
        self, ticker: str, minute: int, intent_us: int, qty: int, facts: dict
    ) -> str | None:
        if self.refusal is not None:
            return "universe_refused"
        if self.kill_path is not None and not entry_control_allowed(self.kill_path):
            return "kill_stop"
        if not self.risk.entries_allowed:
            return "daily_loss_stop"
        if not entries_allowed_at_minute(minute):
            return "flatten_time"
        if ticker in self.active:
            return "overlap_skips"
        if intent_us < self.cooldown_until.get(ticker, -(1 << 62)):
            return "cooldown_skips"
        if int(self.attempts.get(ticker, 0)) >= research.MAX_ATTEMPTS:
            return "max_attempt_skips"
        if qty < 1 or qty * float(facts["limit_price"]) > MAX_ORDER_NOTIONAL:
            return "cash_or_slot_skips"
        reserve = research.reserved_usd(qty, float(facts["limit_price"]))
        if len(self.active) >= research.MAX_SLOTS or self.cash + 1e-9 < reserve:
            return "cash_or_slot_skips"
        return None

    def _process_intent_clock(self, minute: int) -> None:
        rows = self.pending_intents.pop(int(minute), [])
        if not rows:
            return
        intent_us = minute_us(self.day, int(minute))
        admitted: list[dict] = []
        for row in sorted(rows, key=lambda item: (-float(item["pred_60"]), item["ticker"])):
            ticker = row["ticker"]
            stream = self.quotes.stream(ticker)
            facts = research.intent_facts(stream, intent_us)
            state = self._state_row(row, minute, intent_us, facts)
            self._record_state(state)
            reason, quantity = research.arm_eligibility(
                state, self.arm, float(row["pred_60"])
            )
            if reason == "eligible":
                reason = self._book_reason(ticker, minute, intent_us, quantity, facts)
            reserve = (
                research.reserved_usd(quantity, float(facts["limit_price"]))
                if reason is None
                else 0.0
            )
            event = {
                "event": "intent",
                "day": self.day,
                "ticker": ticker,
                "t": int(row["t"]),
                "entry_minute": int(minute),
                "intent_us": int(intent_us),
                "pred_60": float(row["pred_60"]),
                "intent_quote": facts,
                "qty": int(quantity) if reason is None else 0,
                "qty_capital": int(facts["qty_capital"]),
                "limit_price": facts["limit_price"],
                "reserved_usd": reserve,
                "arrival_quote": None,
                "skip_reason": reason,
            }
            if reason is not None:
                self.daily["skip_reasons"][reason] += 1
                self._log(event)
                continue
            self.attempts[ticker] += 1
            self.cash -= reserve
            self.active.add(ticker)
            self.daily["intents_funded"] += 1
            self.daily["attempts"] += 1
            self.daily["deployed_reserved_usd"] += reserve
            self.daily["peak_reserved_usd"] = max(
                self.daily["peak_reserved_usd"],
                float(research.BOOK - self.cash),
            )
            self.daily["_position_samples"].append(len(self.active))
            admitted.append(
                {
                    "state": state,
                    "facts": facts,
                    "qty": int(quantity),
                    "reserve": reserve,
                }
            )
        for item in admitted:
            self._submit_entry(item, positions_at_entry=len(self.active))

    def _submit_entry(self, item: dict, positions_at_entry: int) -> None:
        state = item["state"]
        ticker = state["ticker"]
        intent_us = int(state["intent_us"])
        client_id = f"depthbot-b-{ticker}-{intent_us}"
        record = empty_trade_record(state, item["qty"], self.arm)
        record["attempt_index"] = int(self.attempts[ticker])
        record["positions_at_entry"] = int(positions_at_entry)
        if getattr(self.broker, "mode", "sim") != "sim":
            # A real broker fill is not the simulated outcome: tag it for the parity diff.
            self._exclude(record, f"mode_{getattr(self.broker, 'mode', 'unknown')}")
        order_doc = {
            "client_order_id": client_id,
            "order_side": "buy",
            "type": "limit",
            "time_in_force": "ioc",
            "qty": item["qty"],
            "limit_price": float(state["limit_price"]),
        }
        self._log(
            {
                "event": "intent",
                "day": self.day,
                "ticker": ticker,
                "t": int(state["t"]),
                "entry_minute": int(state["entry_minute"]),
                "intent_us": intent_us,
                "pred_60": float(state["pred_60"]),
                "intent_quote": item["facts"],
                "qty": item["qty"],
                "qty_capital": int(item["facts"]["qty_capital"]),
                "limit_price": state["limit_price"],
                "reserved_usd": item["reserve"],
                "arrival_quote": None,
                "skip_reason": None,
                "order": order_doc,
                "client_order_id": client_id,
            }
        )
        self._log(
            {
                "event": "order_submitted",
                "day": self.day,
                "ticker": ticker,
                "side": "entry",
                "intent_us": intent_us,
                "order": order_doc,
                **order_doc,
            }
        )
        order = self.broker.submit_entry(
            ticker, item["qty"], float(state["limit_price"]), client_id, intent_us
        )
        if self.sim is not None:
            self.sim_attempts.append(
                {
                    "record": record,
                    "ticker": ticker,
                    "qty": item["qty"],
                    "client_id": client_id,
                    "entry_minute": int(state["entry_minute"]),
                    "entry_order": self.sim.submit_entry(
                        ticker, item["qty"], float(state["limit_price"]), client_id, intent_us
                    ),
                    "entry_outcome": None,
                    "due_minute": None,
                    "due_us": None,
                    "exit_order": None,
                    "exit_outcome": None,
                }
            )
        self.pending_entries.append(
            {
                "order": order,
                "record": record,
                "qty": item["qty"],
                "reserve": item["reserve"],
                "client_order_id": client_id,
                "state": state,
            }
        )

    # -- order settlement ------------------------------------------------------
    def _exit_submit_clock(
        self, ticker: str, due_us: int, now_us: int
    ) -> tuple[int, str] | None:
        """The research fresh-gate: submit at the due clock or at the first eligible print."""
        stream = self.quotes.stream(ticker)
        if stream is None:
            return None
        quote, _why = stream.latest_raw(int(due_us))
        if quote is not None and float(quote["age_s"]) <= MAX_AGE_S:
            return int(due_us), "submit_at_due_intent_firm_fresh"
        rested, _why = stream.first_eligible_at_or_after(int(due_us), int(now_us))
        if rested is None:
            return None
        return int(rested["quote_us"]), "submit_at_first_regular_after_due_intent"

    def _poll_orders(self) -> None:
        self._poll_sim()
        if not self.pending_entries and not self.positions:
            return
        for pending in list(self.pending_entries):
            outcome = self.broker.poll_entry(pending["order"], self.now_us)
            if outcome is None:
                continue
            self.pending_entries.remove(pending)
            self._settle_entry(pending, outcome)
        for position in list(self.positions.values()):
            if position.exit_order is None:
                self._maybe_submit_exit(position)
            if position.exit_order is None:
                continue
            outcome = self.broker.poll_exit(position.exit_order, self.now_us)
            if outcome is None:
                continue
            self._settle_exit(position, outcome)

    def _poll_sim(self) -> None:
        """Drive the comparator on its OWN positions, independent of the real account."""
        if self.sim is None:
            return
        for attempt in self.sim_attempts:
            record = attempt["record"]
            if attempt["entry_outcome"] is None:
                outcome = self.sim.poll_entry(attempt["entry_order"], self.now_us)
                if outcome is None:
                    continue
                attempt["entry_outcome"] = outcome
                self._record_sim_entry(record, outcome)
                if outcome["state"] != OPEN_POSITION:
                    continue
                due_minute = min(int(attempt["entry_minute"]) + research.HEAD, self.session_end)
                attempt["due_minute"] = due_minute
                attempt["due_us"] = minute_us(self.day, due_minute)
            if attempt["entry_outcome"]["state"] != OPEN_POSITION:
                continue
            if attempt["exit_outcome"] is not None:
                continue
            if attempt["exit_order"] is None:
                if int(self.now_us) < int(attempt["due_us"]):
                    continue
                clock = self._exit_submit_clock(
                    attempt["ticker"], int(attempt["due_us"]), int(self.now_us)
                )
                if clock is None:
                    continue
                submit_us, status = clock
                record["sim_exit_submit_status"] = status
                attempt["exit_order"] = self.sim.submit_exit(
                    attempt["ticker"], attempt["qty"], f"{attempt['client_id']}-sim", submit_us
                )
            outcome = self.sim.poll_exit(attempt["exit_order"], self.now_us)
            if outcome is None:
                continue
            attempt["exit_outcome"] = outcome
            self._record_sim_exit(record, outcome)

    def _record_sim_entry(self, record: dict, outcome: dict) -> None:
        record["sim_entry_status"] = outcome.get("entry_status")
        record["sim_entry_ask"] = outcome.get("entry_ask")
        record["sim_entry_bid"] = outcome.get("entry_bid")
        record["sim_entry_age_s"] = outcome.get("entry_age_s")
        record["sim_entry_quote_us"] = outcome.get("entry_quote_us")
        if outcome["state"] == OPEN_POSITION:
            record["sim_fill_status"] = "conditional_fill"
        elif outcome["state"] == UNFILLED:
            record["sim_fill_status"] = "no_match_at_l1_unfilled_cash"
        else:
            record["sim_fill_status"] = "unknown_entry_execution"
        self._settle_sim_verdict(record)

    def _record_sim_exit(self, record: dict, outcome: dict) -> None:
        if record.get("sim_entry_status") != "conditional_ioc_fill":
            return  # the simulated arm never had a position: its entry verdict stands
        record["sim_exit_status"] = outcome.get("exit_status")
        record["sim_exit_bid"] = outcome.get("exit_bid")
        record["sim_exit_bid_shares"] = outcome.get("exit_bid_shares")
        record["sim_exit_quote_us"] = outcome.get("exit_quote_us")
        if outcome["state"] == "closed":
            record["sim_fill_status"] = "conditional_fill"
        else:
            record["sim_fill_status"] = "unknown_exit_execution"
        self._settle_sim_verdict(record)

    def _settle_sim_verdict(self, record: dict) -> None:
        status = record.get("sim_fill_status")
        if status is None:
            return
        record["sim_would_be_unknown"] = "unknown" in status
        if status == "conditional_fill":
            quantity = int(record.get("qty") or 0)
            entry_ask = record.get("sim_entry_ask")
            exit_bid = record.get("sim_exit_bid")
            if entry_ask is not None and exit_bid is not None:
                for rung in (0.0, 25.0):
                    record[f"sim_net_usd_{int(rung)}"] = research.net_usd(
                        quantity, float(exit_bid), float(entry_ask), rung
                    )

    def _arrival_quote(self, ticker: str, arrival: int) -> dict | None:
        stream = self.quotes.stream(ticker)
        if stream is None:
            return None
        quote, _status = stream.latest_raw(int(arrival))
        return quote

    def _settle_entry(self, pending: dict, outcome: dict) -> None:
        record = pending["record"]
        state = outcome["state"]
        record["entry_status"] = outcome["entry_status"]
        arrive = int(record["entry_arrival_us"])
        arrival = self._arrival_quote(pending["state"]["ticker"], arrive)
        if state == OPEN_POSITION:
            # The research record carries the priced entry ONLY for an executed IOC.
            if arrival is not None:
                record["entry_ask"] = float(arrival["ask"])
                record["entry_bid"] = float(arrival["bid"])
                record["entry_age_s"] = float(arrival["age_s"])
                record["entry_quote_us"] = int(arrival["quote_us"])
            for key in ("entry_ask", "entry_bid", "entry_age_s", "entry_quote_us"):
                if outcome.get(key) is not None:
                    record[key] = outcome[key]
        if outcome.get("broker_order_id"):
            record["broker_order_id"] = outcome["broker_order_id"]
        if outcome.get("filled_at_us"):
            record["broker_fill_us"] = int(outcome["filled_at_us"])
        if outcome.get("filled_avg_price") is not None:
            record["broker_fill_price"] = float(outcome["filled_avg_price"])
            record["broker_entry_price"] = float(outcome["filled_avg_price"])
        if outcome.get("filled_qty") is not None:
            record["broker_filled_qty"] = int(outcome["filled_qty"])
        if outcome.get("partial"):
            record["partial_entry"] = True
            self._exclude(record, "partial_entry")
        self._log(
            {
                "event": "entry_result",
                "day": self.day,
                "ticker": record["ticker"],
                "client_order_id": pending["client_order_id"],
                "state": state,
                "entry_status": record["entry_status"],
                "arrival_quote": {
                    "entry_ask": record["entry_ask"],
                    "entry_bid": record["entry_bid"],
                    "entry_age_s": record["entry_age_s"],
                    "entry_quote_us": record["entry_quote_us"],
                    "entry_arrival_us": record["entry_arrival_us"],
                },
                "partial_entry": record["partial_entry"],
            }
        )
        if (
            state != OPEN_POSITION
            and self.mode == "paper"
            and record["entry_status"] not in {"no_match_at_l1", "conditional_ioc_fill"}
        ):
            self._alert(
                "broker_unexpected",
                scope="entry",
                ticker=record["ticker"],
                status=record["entry_status"],
            )
        if state == OPEN_POSITION:
            quantity = int(outcome.get("filled_qty") or pending["qty"])
            broker_price = outcome.get("filled_avg_price")
            entry_price = broker_price if broker_price is not None else record["entry_ask"]
            entry_minute = int(record["entry_minute"])
            fill_us = int(outcome.get("filled_at_us") or arrive)
            if outcome.get("filled_at_us"):
                entry_minute = max(entry_minute, et_minute_of(fill_us))
            exit_minute = min(entry_minute + research.HEAD, self.session_end)
            position = Position(
                ticker=record["ticker"],
                qty=quantity,
                entry_minute=entry_minute,
                exit_minute=exit_minute,
                reserved_usd=float(pending["reserve"]),
                client_order_id=pending["client_order_id"],
                due_us=minute_us(self.day, exit_minute),
                entry_price=float(entry_price or 0.0),
                record=record,
            )
            self.positions[position.ticker] = position
            record["fill_status"] = "conditional_fill"
            self._log(
                {
                    "event": "fill",
                    "day": self.day,
                    "ticker": record["ticker"],
                    "side": "buy",
                    "qty": quantity,
                    "price": position.entry_price,
                    "client_order_id": pending["client_order_id"],
                    "entry_minute": entry_minute,
                    "exit_minute": exit_minute,
                    "partial": record["partial_entry"],
                }
            )
            return
        if state == UNFILLED:
            record["fill_status"] = "no_match_at_l1_unfilled_cash"
            # The frozen rule releases the ticket at the ARRIVAL clock, never at the
            # (later) clock that happened to observe the cancellation.
            record["actual_exit_us"] = arrive
            self.daily["no_match_fills"] += 1
            self._schedule(arrive, RELEASE_PRIORITY, "release", {
                "ticker": record["ticker"],
                "amount": float(pending["reserve"]),
                "cooldown": False,
            })
        else:
            record["fill_status"] = "unknown_entry_execution"
            record["exit_status"] = record["entry_status"]
            record["actual_exit_us"] = self.session_end_us + 1
            self.daily["unknown_fills"] += 1
            self._schedule(int(outcome.get("release_us") or self.session_end_us + 1),
                           RELEASE_PRIORITY, "release", {
                               "ticker": record["ticker"],
                               "amount": float(pending["reserve"]),
                               "cooldown": True,
                           })
        self._finish_trade(record)

    def _maybe_submit_exit(self, position: Position) -> None:
        if position.retry_blocked:
            return  # the retry cap was hit; only the alert path remains
        if int(self.now_us) < int(position.next_retry_us):
            return  # exponential backoff between retries
        if int(self.now_us) < int(position.due_us) and not position.forced:
            return
        submit_us = int(position.due_us)
        status: str | None = None
        if position.forced:
            submit_us = int(self.now_us)
            status = "flatten_forced"
        else:
            clock = self._exit_submit_clock(position.ticker, submit_us, int(self.now_us))
            if clock is None:
                return
            submit_us, status = clock
        if status == "flatten_forced":
            self._exclude(position.record, "flatten_forced")
        client_id = f"depthbot-s-{position.ticker}-{submit_us}"
        position.exit_client_id = client_id
        position.record["exit_submit_us"] = submit_us
        position.record["exit_submit_status"] = status
        position.record["exit_arrival_us"] = arrival_us(submit_us)
        self._log(
            {
                "event": "order_submitted",
                "day": self.day,
                "ticker": position.ticker,
                "side": "exit",
                "client_order_id": client_id,
                "qty": position.qty,
                "type": "market",
                "time_in_force": "day",
                "submit_us": submit_us,
                "submit_status": status,
            }
        )
        position.exit_order = self.broker.submit_exit(
            position.ticker, position.qty, client_id, submit_us
        )

    def _settle_exit(self, position: Position, outcome: dict) -> None:
        record = position.record
        for key in (
            "exit_bid",
            "exit_bid_shares",
            "exit_age_s",
            "exit_quote_us",
            "exit_priced_at_status",
            "exit_status",
            "exit_arrival_us",
            "actual_exit_us",
        ):
            if outcome.get(key) is not None:
                record[key] = outcome[key]
        if outcome.get("broker_order_id"):
            record["broker_order_id"] = outcome["broker_order_id"]
        if outcome["state"] == "retry":
            # The real order died without a fill: keep the position, but never loop on it.
            position.retries += 1
            record["exit_status"] = outcome.get("exit_status")
            position.exit_order = None
            position.exit_client_id = None
            position.due_us = int(self.now_us)
            if position.retries > EXIT_RETRY_MAX:
                position.retry_blocked = True
                self._log(
                    {
                        "event": "exit_retry_exhausted",
                        "day": self.day,
                        "ticker": position.ticker,
                        "qty": position.qty,
                        "retries": position.retries - 1,
                        "status": outcome.get("exit_status"),
                    }
                )
                self._alert(
                    "exit_retry_exhausted",
                    ticker=position.ticker,
                    qty=position.qty,
                    retries=position.retries - 1,
                    status=outcome.get("exit_status"),
                )
                return
            position.next_retry_us = int(self.now_us) + EXIT_RETRY_BACKOFF_US * (
                2 ** (position.retries - 1)
            )
            self._log(
                {
                    "event": "exit_retry",
                    "day": self.day,
                    "ticker": position.ticker,
                    "qty": position.qty,
                    "status": outcome.get("exit_status"),
                    "broker_status": outcome.get("broker_status"),
                    "retry_index": position.retries,
                    "next_retry_us": position.next_retry_us,
                }
            )
            self._alert(
                "exit_retry",
                ticker=position.ticker,
                qty=position.qty,
                status=outcome.get("exit_status"),
                retry_index=position.retries,
            )
            return
        if self.mode == "paper" and outcome["state"] != "closed":
            # A real position cannot be booked flat on a non-fill; keep it open so the
            # flatten path and position_not_flattened can act on it.
            position.exit_order = None
            position.exit_client_id = None
            position.due_us = int(self.now_us)
            self._log(
                {
                    "event": "exit_unconfirmed",
                    "day": self.day,
                    "ticker": position.ticker,
                    "qty": position.qty,
                    "status": outcome.get("exit_status"),
                }
            )
            return
        if self.mode == "paper" and str(record.get("exit_status") or "").startswith("exit_"):
            self._alert(
                "broker_unexpected",
                scope="exit",
                ticker=position.ticker,
                status=record["exit_status"],
            )
        if outcome["state"] == "closed":
            broker_price = outcome.get("filled_avg_price")
            entry_price = (
                position.entry_price if position.entry_price is not None else record["entry_ask"]
            )
            exit_price = broker_price if broker_price is not None else record["exit_bid"]
            quantity = min(int(outcome.get("filled_qty") or position.qty), position.qty)
            tranche = (
                quantity * (float(exit_price) - float(entry_price))
                if exit_price is not None and (entry_price or 0) > 0
                else 0.0
            )
            position.filled_qty += quantity
            position.realized_usd += tranche
            if exit_price is not None:
                position.exit_notional += quantity * float(exit_price)
            remaining = position.qty - quantity
            if remaining > 0:
                # A partial exit is a real (live-only) state: journal it, then keep working
                # the remainder as a marketable order until it fills or 15:55 flatten.
                position.partials += 1
                self._exclude(record, "partial_exit")
                self._log(
                    {
                        "event": "exit_partial",
                        "day": self.day,
                        "ticker": position.ticker,
                        "client_order_id": position.exit_client_id,
                        "qty": quantity,
                        "remaining": remaining,
                        "price": exit_price,
                        "exit_bid": record.get("exit_bid"),
                        "realized_pnl_usd": tranche,
                        "partial_index": position.partials,
                    }
                )
                position.qty = remaining
                position.exit_order = None
                position.exit_client_id = None
                position.due_us = int(self.now_us)
                if self.now_us >= minute_us(self.day, self.session_end):
                    self._alert(
                        "partial_exit_unfilled",
                        ticker=position.ticker,
                        remaining=remaining,
                    )
                return
            record["fill_status"] = "conditional_fill"
            if record["exit_bid"] is not None and (record.get("entry_ask") or 0) > 0:
                for rung in research.RUNG_COSTS:
                    record[f"net_usd_{int(rung)}"] = research.net_usd(
                        position.filled_qty,
                        float(record["exit_bid"]),
                        float(record["entry_ask"]),
                        rung,
                    )
            record["broker_exit_price"] = None if broker_price is None else float(broker_price)
            record["broker_filled_qty"] = (
                None if outcome.get("filled_qty") is None else position.filled_qty
            )
            record["realized_pnl_usd"] = position.realized_usd
            self.daily["fills"] += 1
            self.daily["known_fills"] += 1
            self._close_position(
                position,
                position.realized_usd,
                outcome,
                int(record["actual_exit_us"] or self.session_end_us + 1),
            )
            return
        else:
            record["fill_status"] = "unknown_exit_execution"
            realized = position.realized_usd
            self.daily["unknown_fills"] += 1
        actual_exit_us = int(record["actual_exit_us"] or self.session_end_us + 1)
        self._close_position(position, realized, outcome, actual_exit_us)

    def _close_position(
        self, position: Position, realized: float, outcome: dict, actual_exit_us: int
    ) -> None:
        record = position.record
        record["actual_exit_us"] = int(actual_exit_us)
        record["realized_pnl_usd"] = float(realized)
        closed_qty = position.filled_qty or position.qty
        self.positions.pop(position.ticker, None)
        self.daily["realized_pnl_usd"] += float(realized)
        was_stopped = self.risk.stopped
        self.risk.record_realized(realized)
        self.daily["risk_stopped"] = self.risk.stopped
        self._log(
            {
                "event": "position_closed",
                "day": self.day,
                "ticker": position.ticker,
                "side": "sell",
                "qty": closed_qty,
                "exit_bid": record.get("exit_bid"),
                "sim_exit_bid": record.get("sim_exit_bid"),
                "broker_exit_price": record.get("broker_exit_price"),
                "entry_slippage": (
                    float(record["broker_fill_price"]) - float(record["entry_ask"])
                    if record.get("broker_fill_price") is not None
                    and record.get("entry_ask") is not None
                    else None
                ),
                "exit_slippage": (
                    float(record["broker_exit_price"]) - float(record["sim_exit_bid"])
                    if record.get("broker_exit_price") is not None
                    and record.get("sim_exit_bid") is not None
                    else None
                ),
                "realized_pnl_usd": float(realized),
                "actual_exit_us": int(actual_exit_us),
                "fill_status": record.get("fill_status"),
                "exit_status": outcome.get("exit_status") or record.get("exit_status"),
                "client_order_id": position.exit_client_id or position.client_order_id,
                "late_exit": bool(record.get("late_exit")),
            }
        )
        if self.risk.stopped and not was_stopped:
            self._log(
                {
                    "event": "risk_stop",
                    "day": self.day,
                    "reason": "daily_loss_stop",
                    "realized_pnl_usd": self.risk.realized_usd,
                    "limit_usd": self.risk.limit_usd,
                }
            )
            self._alert(
                "loss_stop",
                realized_pnl_usd=self.risk.realized_usd,
                limit_usd=self.risk.limit_usd,
            )
        self._schedule(
            int(actual_exit_us),
            RELEASE_PRIORITY,
            "release",
            {
                "ticker": position.ticker,
                "amount": float(position.reserved_usd),
                "cooldown": True,
            },
        )
        self._finish_trade(record)

    def _finish_trade(self, record: dict) -> None:
        if record.get("parity_excluded"):
            self.daily["parity_excluded"] += 1
        self.trades.append(record)
        if self.sim is None:
            self._log({"event": "trade_result", **record})
        # In paper mode the record is journalled at the close instead: the simulated arm's
        # verdict for the SAME attempt may still be resolving, and it must not be lost.

    def _close_sim_attempts(self) -> None:
        """Settle the comparator at the close, independently of the real account."""
        if self.sim is None:
            return
        for attempt in self.sim_attempts:
            record = attempt["record"]
            if attempt["entry_outcome"] is None:
                outcome = self.sim.poll_entry(attempt["entry_order"], self.session_end_us)
                if outcome is None:
                    outcome = self.sim.close_entry(attempt["entry_order"], self.session_end_us)
                attempt["entry_outcome"] = outcome
                self._record_sim_entry(record, outcome)
            if (
                attempt["entry_outcome"]["state"] == OPEN_POSITION
                and attempt["exit_outcome"] is None
            ):
                if attempt["exit_order"] is None:
                    clock = self._exit_submit_clock(
                        attempt["ticker"], int(attempt["due_us"]), self.session_end_us
                    )
                    if clock is not None:
                        attempt["exit_order"] = self.sim.submit_exit(
                            attempt["ticker"],
                            attempt["qty"],
                            f"{attempt['client_id']}-sim",
                            clock[0],
                        )
                outcome = None
                if attempt["exit_order"] is not None:
                    outcome = self.sim.poll_exit(attempt["exit_order"], self.session_end_us)
                    if outcome is None:
                        outcome = self.sim.close_exit(
                            attempt["exit_order"], self.session_end_us
                        )
                if outcome is None:
                    outcome = {
                        "state": UNKNOWN,
                        "exit_status": "unknown_exit_no_valid_regular_quote",
                        "actual_exit_us": self.session_end_us + 1,
                    }
                attempt["exit_outcome"] = outcome
                self._record_sim_exit(record, outcome)

    def _release(self, clock_us: int, payload: dict) -> None:
        ticker = payload["ticker"]
        self.active.discard(ticker)
        self.cash += float(payload["amount"])
        if payload.get("cooldown"):
            # Anchored at the RELEASE clock, not at the (later) clock that drained the task.
            self.cooldown_until[ticker] = int(clock_us) + research.COOLDOWN_MIN * 60_000_000

    def _flatten(self) -> None:
        if not self.positions:
            return
        for position in self.positions.values():
            if position.exit_order is not None:
                continue
            position.forced = True
            self._maybe_submit_exit(position)

    def _drain_tasks(self) -> None:
        while self.tasks and self.tasks[0][0] <= self.now_us:
            clock, _priority, _seq, kind, payload = heapq.heappop(self.tasks)
            if kind == "release":
                self._release(clock, payload)
            elif kind == "intent":
                self._process_intent_clock(int(payload))
            elif kind == "snapshot":
                self.guard_snapshot(int(payload))
            elif kind == "flatten":
                self._flatten()

    # -- shutdown --------------------------------------------------------------
    def finish(self) -> tuple[dict, list[dict]]:
        self.now_us = max(self.now_us, self.session_end_us)
        self._poll_orders()  # last comparator/real order read at the session clock
        for minute in sorted(self._bar_buffer):
            self._flush_minute(minute, decide=False)
        # Any entry whose arrival never produced a print is the research UNKNOWN.
        for pending in list(self.pending_entries):
            outcome = self.broker.poll_entry(pending["order"], self.session_end_us)
            if outcome is None:
                outcome = self.broker.close_entry(pending["order"], self.session_end_us)
            self.pending_entries.remove(pending)
            self._settle_entry(pending, outcome)
        for position in list(self.positions.values()):
            if position.exit_order is None:
                self._maybe_submit_exit(position)
            outcome = None
            if position.exit_order is not None:
                outcome = self.broker.poll_exit(position.exit_order, self.session_end_us)
                if outcome is None:
                    outcome = self.broker.close_exit(position.exit_order, self.session_end_us)
            if outcome is None:
                outcome = {
                    "state": UNKNOWN,
                    "exit_status": "unknown_exit_no_valid_regular_quote",
                    "actual_exit_us": self.session_end_us + 1,
                }
            self._settle_exit(position, outcome)
        self._close_sim_attempts()
        self._drain_tasks()
        # A real position still open at the close is unresolved, not absent: it gets its own
        # trade_result so the parity diff can see it.
        for position in list(self.positions.values()):
            record = position.record
            record["fill_status"] = "unresolved_open"
            record["actual_exit_us"] = self.session_end_us + 1
            self._exclude(record, "unresolved_open")
            self._log(
                {
                    "event": "position_unresolved",
                    "day": self.day,
                    "ticker": position.ticker,
                    "qty": position.qty,
                    "exit_status": record.get("exit_status"),
                }
            )
            self._finish_trade(record)
        samples = self.daily.pop("_position_samples", [])
        self.daily["positions_peak"] = max(samples) if samples else 0
        self.daily["positions_mean"] = float(sum(samples) / len(samples)) if samples else 0.0
        self.daily["trade_count"] = len(self.trades)
        self.daily["mode"] = getattr(self.broker, "mode", "unknown")
        # Attempts are reported in INTENT order, never in settlement order.
        self.trades.sort(key=lambda record: (int(record["intent_us"]), str(record["ticker"])))
        if self.sim is not None:
            for record in self.trades:
                self._log({"event": "trade_result", **record})
        self.write_states()
        summary = dict(self.daily)
        summary["skip_reasons"] = dict(self.daily["skip_reasons"])
        self._log({"event": "daily_summary", **summary})
        if self.positions:  # pragma: no cover - only reachable on a broker failure
            self._alert(
                "position_not_flattened",
                tickers=sorted(self.positions),
                quantities={ticker: position.qty for ticker, position in self.positions.items()},
            )
        self._log(
            {
                "event": "session_end",
                "day": self.day,
                "mode": summary["mode"],
                "trade_count": summary["trade_count"],
                "realized_pnl_usd": summary["realized_pnl_usd"],
                "refusal": summary["refusal"],
                "parity_excluded": summary["parity_excluded"],
            }
        )
        return summary, self.trades


# ----- stream sources ---------------------------------------------------------
class ReplayStreamSource:
    """Stored SIP day delivered as one clock-ordered event stream (never a network read)."""

    def __init__(self, bars: pl.DataFrame, quotes: pl.DataFrame | None, day: str):
        self.bars = bars
        self.quotes = quotes
        self.day = day

    def _bar_events(self) -> list[tuple[int, pl.DataFrame]]:
        groups = []
        if self.bars.height:
            for (et,), part in self.bars.sort(["et", "ticker"]).partition_by(
                "et", as_dict=True
            ).items():
                groups.append((minute_us(self.day, int(et) + 1), part))
        groups.sort(key=lambda item: item[0])
        return groups

    def _quote_events(self) -> Iterator[dict]:
        if self.quotes is None or not self.quotes.height:
            return iter(())
        frame = self.quotes.sort(["ts_utc", "symbol"])
        columns = {
            name: frame[name].to_list()
            for name in ("symbol", "bid_price", "ask_price", "bid_size", "ask_size", "conditions")
        }
        stamps = frame["ts_utc"].cast(pl.Int64).to_numpy()
        symbols = columns["symbol"]
        bids = columns["bid_price"]
        asks = columns["ask_price"]
        bid_sizes = columns["bid_size"]
        ask_sizes = columns["ask_size"]
        conditions = columns["conditions"]
        for index in range(frame.height):
            yield {
                "symbol": symbols[index],
                "ts_utc": int(stamps[index]),
                "bid_price": bids[index],
                "ask_price": asks[index],
                "bid_size": bid_sizes[index],
                "ask_size": ask_sizes[index],
                "conditions": conditions[index],
            }

    def run(self, on_bar, on_quote) -> None:
        bar_events = self._bar_events()
        quotes = self._quote_events()
        bar_i = 0
        quote_row = next(quotes, None)
        while bar_i < len(bar_events) or quote_row is not None:
            bar_clock = bar_events[bar_i][0] if bar_i < len(bar_events) else None
            quote_clock = ts_us_of(quote_row["ts_utc"]) if quote_row is not None else None
            if bar_clock is not None and (quote_clock is None or bar_clock <= quote_clock):
                on_bar(bar_events[bar_i][1])
                bar_i += 1
            else:
                on_quote(quote_row)
                quote_row = next(quotes, None)


def default_stream_factory(key: str, secret: str):
    from alpaca.data.enums import DataFeed
    from alpaca.data.live import StockDataStream

    return StockDataStream(key, secret, feed=DataFeed.SIP)


class StreamEndedEarlyError(RuntimeError):
    """The venue socket returned before the session end and could not be revived."""


class AlpacaSIPStream:
    """Live SIP stream in a WORKER thread; the caller's thread owns subscriptions + close.

    alpaca-py 0.43.4 ``subscribe_quotes`` blocks on ``run_coroutine_threadsafe(...).result()``,
    so calling it from inside a stream handler deadlocks the event loop. Handlers therefore
    only enqueue the names the engine admitted (under a lock), the caller's thread subscribes
    them, and at ``session_end_us`` the caller stops the socket and returns so the day can be
    finished, captured and rolled.

    A socket that returns BEFORE the session end is never silent: the reason and error are
    recorded, an ``ALERT stream_ended_early`` is written, and the socket is re-created with an
    exponential backoff. Only a revive that survives to ``session_end_us`` lets the day finish.
    """

    def __init__(
        self,
        key: str,
        secret: str,
        universe: list[str],
        *,
        session_end_us: int,
        stream_factory: Callable[[str, str], Any] | None = None,
        poll_s: float = 0.2,
        sleep: Callable[[float], None] = time.sleep,
        now_us: Callable[[], int] = lambda: int(time.time() * 1_000_000),
        alert_path: Path | None = None,
        max_reconnects: int = 5,
        backoff_s: float = 2.0,
    ):
        self.key = key
        self.secret = secret
        self.universe = list(universe)
        self.session_end_us = int(session_end_us)
        self.stream_factory = stream_factory or default_stream_factory
        self.poll_s = float(poll_s)
        self.sleep = sleep
        self.now_us = now_us
        self.alert_path = Path(alert_path) if alert_path is not None else None
        self.max_reconnects = int(max_reconnects)
        self.backoff_s = float(backoff_s)
        self.subscribed: set[str] = set()
        self.sessions = 0
        self.reconnects = 0
        self.stop_reason: str | None = None
        self.error: BaseException | None = None

    def run(self, on_bar, on_quote) -> None:
        while True:
            self._run_socket(on_bar, on_quote)
            if self.stop_reason == "session_end":
                return
            if int(self.now_us()) >= self.session_end_us:
                self.stop_reason = self.stop_reason or "session_end"
                return
            self.reconnects += 1
            reason = self.stop_reason or "socket_returned_early"
            write_alert(
                self.alert_path,
                "stream_ended_early",
                reason=reason,
                error=None if self.error is None else repr(self.error),
                attempt=self.reconnects,
                resume_at_us=int(self.now_us()),
                session_end_us=self.session_end_us,
            )
            self.error = self.error or RuntimeError(f"socket returned before session end: {reason}")
            if self.reconnects > self.max_reconnects:
                self.stop_reason = "reconnect_exhausted"
                write_alert(
                    self.alert_path,
                    "stream_reconnect_exhausted",
                    attempts=self.reconnects - 1,
                    error=repr(self.error),
                )
                raise StreamEndedEarlyError(str(self.error)) from self.error
            self.sleep(self.backoff_s * (2 ** (self.reconnects - 1)))
            self.error = None
            self.stop_reason = None

    def _run_socket(self, on_bar, on_quote) -> None:
        stream = self.stream_factory(self.key, self.secret)
        pending: list[str] = []
        pending_lock = threading.Lock()
        subscribed: set[str] = set()

        def enqueue(names) -> None:
            with pending_lock:
                pending.extend(sorted(set(names)))

        async def bar_handler(bar):
            clock = bar.timestamp.astimezone(ET)
            row = {
                "ticker": bar.symbol,
                "et": clock.hour * 60 + clock.minute,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
            }
            added = on_bar(pl.DataFrame([row]))
            if added:
                enqueue(name for name in added if name not in subscribed)

        async def quote_handler(quote):
            added = on_quote(
                {
                    "symbol": quote.symbol,
                    "ts_utc": quote.timestamp,
                    "bid_price": quote.bid_price,
                    "ask_price": quote.ask_price,
                    "bid_size": quote.bid_size,
                    "ask_size": quote.ask_size,
                    "bid_exchange": quote.bid_exchange,
                    "ask_exchange": quote.ask_exchange,
                    "conditions": list(quote.conditions or []),
                }
            )
            if added:
                enqueue(name for name in added if name not in subscribed)

        stream.subscribe_bars(bar_handler, *self.universe)
        if self.subscribed:
            stream.subscribe_quotes(quote_handler, *sorted(self.subscribed))
            subscribed.update(self.subscribed)

        def worker() -> None:
            try:
                stream.run()
            except BaseException as exc:  # noqa: BLE001 - surfaced to the caller below
                self.error = exc

        thread = threading.Thread(target=worker, name="depthbot-sip", daemon=True)
        thread.start()
        self.sessions += 1
        try:
            while thread.is_alive():
                # Drain atomically: a handler may append while we read.
                with pending_lock:
                    names = sorted(set(pending) - subscribed)
                    pending.clear()
                if names:
                    # Never from inside a handler: this runs on the caller's thread.
                    stream.subscribe_quotes(quote_handler, *names)
                    subscribed.update(names)
                if int(self.now_us()) >= self.session_end_us:
                    self.stop_reason = "session_end"
                    break
                self.sleep(self.poll_s)
            else:
                # The worker thread is gone: the socket returned before the session end.
                if int(self.now_us()) < self.session_end_us and self.stop_reason is None:
                    self.stop_reason = "socket_returned_early"
        finally:
            self.subscribed |= subscribed
            with contextlib.suppress(Exception):  # a dead socket is already stopped
                stream.stop()
            thread.join(timeout=10.0)


# ----- reference data ---------------------------------------------------------
SYMBOL_PATTERN = re.compile(r"^[A-Z]+$")


def load_pit_universe(day: str, data_root: Path = DATA_ROOT) -> list[str]:
    """Today's PIT symbols that Alpaca can actually subscribe.

    The SIP bar websocket rejects the WHOLE subscription with "invalid syntax (400)" when any
    symbol is non-alphabetic, and the PIT list carries preferred/warrant/class forms
    (``BRK/A``, ``BAC^E``). Such tickers are dropped here, before the bar subscription.
    """
    path = Path(data_root) / "pit" / "pit_symbols.parquet"
    if not path.is_file():
        raise FileNotFoundError(f"PIT symbol universe missing: {path}")
    table = pl.read_parquet(path, columns=["vintage", "symbol"])
    eligible = table.filter(pl.col("vintage") <= day)
    if eligible.is_empty():
        raise ValueError(f"no PIT universe vintage exists on or before {day}")
    latest = eligible["vintage"].max()
    symbols = set(eligible.filter(pl.col("vintage") == latest)["symbol"].to_list())
    return sorted(symbol for symbol in symbols if SYMBOL_PATTERN.match(str(symbol)))


def session_end_for_day(day: str) -> int:
    calendar = ROOT / "factory" / "artifacts" / "basket" / "sip" / (
        "phase2_session_calendar.json"
    )
    data = json.loads(calendar.read_text(encoding="utf-8"))
    return int(data["session_end_by_day"].get(day, data["session_end_normal"]))


def _load_credentials() -> tuple[str, str]:
    """Read only this repository's .env without echoing, logging or exporting its values."""
    env_path = ROOT / ".env"
    values: dict[str, str] = {}
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"'")
    key = values.get("APCA_API_KEY_ID") or values.get("ALPACA_API_KEY")
    secret = values.get("APCA_API_SECRET_KEY") or values.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("paper credentials missing from repository .env")
    return key, secret


def journal_file_for(journal_root: Path, day: str) -> Path:
    return Path(journal_root) / f"{day}.jsonl"


def states_path_for(
    journal_root: Path, states_root: Path | None, day: str
) -> Path:
    root = Path(states_root) if states_root is not None else Path(journal_root).parent / "states"
    return Path(root) / f"{day}.parquet"


def write_capture(
    capture: dict[str, Any], day: str, journal_root: Path, data_root: Path = DATA_ROOT
) -> None:
    """Persist the bars and admitted-name prints this run actually consumed."""
    root = Path(journal_root).parent
    bars = capture.get("bars")
    if bars is not None and bars.height:
        bars_dir = root / "bars"
        bars_dir.mkdir(parents=True, exist_ok=True)
        bars.write_parquet(bars_dir / f"{day}.parquet")
    cache = capture.get("quote_cache")
    if cache is not None:
        for ticker, frame in cache.capture_frames():
            quote_dir = root / "quotes" / str(ticker)
            quote_dir.mkdir(parents=True, exist_ok=True)
            frame.write_parquet(quote_dir / f"{day}.parquet")


# ----- modes ------------------------------------------------------------------
def replay_day(
    day: str,
    state_root: Path | None = None,
    data_root: Path = DATA_ROOT,
    journal_root: Path = JOURNAL_ROOT,
    arm=ARM,
    source: ReplayStreamSource | None = None,
    session_end: int | None = None,
    model=None,
    broker: Broker | None = None,
    states_root: Path | None = None,
    universe: list[str] | None = None,
) -> tuple[dict, list[dict]]:
    """Drive the live decision engine over a stored (or injected) SIP day."""
    data_root = Path(data_root)
    if source is None and not research.allowed(day):
        raise ValueError(f"protected/out-of-scope replay day refused: {day}")
    if session_end is None:
        session_end = session_end_for_day(day)
    if source is None:
        bars_path = data_root / "sip" / "net" / "bars" / f"{day}.parquet"
        if not bars_path.is_file():
            raise FileNotFoundError(f"stored SIP bars missing: {bars_path}")
        bars = pl.read_parquet(bars_path).select(
            "ticker", "et", "open", "high", "low", "close", "volume"
        )
        if not day_quote_path(data_root, day).is_file():
            raise FileNotFoundError(f"stored SIP quote stream missing for {day}")
        quotes = quote_day_frame(
            data_root, day, set(bars["ticker"].to_list()), research.SUPPLEMENT_ROOTS
        )
        source = ReplayStreamSource(bars, quotes, day)
    model = model or depth_bot_features.load_model()
    quotes = QuoteCache(day)
    if universe is None and getattr(source, "bars", None) is not None:
        universe = sorted(set(source.bars["ticker"].to_list()))
    if broker is None:
        broker = SimBroker(day, quotes, minute_us(day, int(session_end)))
    engine = DepthEngine(
        day,
        int(session_end),
        model,
        broker,
        journal_file_for(journal_root, day),
        arm=arm,
        fresh=True,
        quotes=quotes,
        kill_path=None,  # an offline replay is a what-if, never an order path
        states_path=states_path_for(journal_root, states_root, day),
        universe=universe,
        mode="sim",
    )
    source.run(engine.on_bar, engine.on_quote)
    daily, trades = engine.finish()
    write_capture(
        {"bars": pl.DataFrame(engine.bar_rows) if engine.bar_rows else None,
         "quote_cache": engine.quotes},
        day,
        journal_root,
        data_root,
    )
    return daily, trades


def reconcile_positions(
    journal_file: Path,
    broker_positions: dict[str, dict],
    broker_fills: dict[str, dict] | None = None,
) -> dict[str, dict]:
    """Adopt broker positions with the broker's own entry clock and price.

    The journal is preferred when it has a fill for today; otherwise the broker order's
    ``filled_at``/``filled_avg_price`` is used, so an overnight position is never adopted with
    a skeleton ``entry_minute=0`` record.
    """
    broker_fills = broker_fills or {}
    saved = {}
    for event in read_events(journal_file):
        if event.get("event") in {"position_open", "fill"}:
            saved[event["ticker"]] = event
        elif event.get("event") == "position_closed":
            saved.pop(event.get("ticker"), None)
    adopted: dict[str, dict] = {}
    for ticker, position in broker_positions.items():
        prior = saved.get(ticker, {})
        fill = broker_fills.get(ticker, {})
        entry_minute = int(prior.get("entry_minute", fill.get("entry_minute", 0)) or 0)
        entry_price = float(
            prior.get("price")
            or prior.get("entry_price")
            or fill.get("entry_price")
            or position.get("entry_price", 0.0)
        )
        adopted[ticker] = {
            "qty": int(position.get("qty", 0)),
            "entry_price": entry_price,
            "entry_minute": entry_minute,
            "entry_clock_known": bool(entry_minute),
            "exit_minute": int(prior.get("exit_minute", 0)),
            "reserved_usd": float(
                prior.get(
                    "reserved_usd",
                    float(position.get("qty", 0))
                    * entry_price
                    * research.FUNDING_SIDE_FACTOR,
                )
            ),
            "client_order_id": prior.get("client_order_id") or fill.get("client_order_id"),
        }
    for ticker in sorted(set(adopted) - set(saved)):
        append_event(
            journal_file,
            {"event": "reconcile_adopt", "ticker": ticker, "source": "broker_position"},
        )
    return adopted


def run_live(
    mode: str,
    paper_flag: bool,
    day: str | None = None,
    source=None,
    broker=None,
    journal_root: Path = JOURNAL_ROOT,
    session_end: int | None = None,
    model=None,
    states_root: Path | None = None,
    alert_path: Path | None = None,
    history: HistorySource | None = None,
    universe: list[str] | None = None,
    resume_us: int | None = None,
    kill_path: Path | None = None,
) -> tuple[dict, list[dict]]:
    """Run one session: shadow never orders, paper requires --paper AND the ARMED file."""
    kill_file = Path(kill_path) if kill_path is not None else KILL_PATH
    if mode not in {"shadow", "paper"}:
        raise ValueError(f"unsupported live mode: {mode}")
    day = day or datetime.now(ET).date().isoformat()
    if mode == "paper" and not paper_orders_allowed(paper_flag, ARMED_PATH):
        raise RuntimeError(
            "paper orders disabled: requires --paper and data/forward/depth_bot/ARMED"
        )
    journal_root = Path(journal_root)
    journal_file = journal_file_for(journal_root, day)
    session_end = int(session_end or session_end_for_day(day))
    alerts = Path(alert_path) if alert_path is not None else journal_root.parent / "ALERT"
    key = secret = None
    if source is None:
        key, secret = _load_credentials()
        universe = universe or load_pit_universe(day)
        history = history or AlpacaHistory(key, secret)
        source = AlpacaSIPStream(
            key,
            secret,
            universe,
            session_end_us=minute_us(day, session_end),
            alert_path=alerts,
        )
    open_positions: dict[str, dict] = {}
    if mode == "paper" and broker is None:
        from alpaca.trading.client import TradingClient

        client = TradingClient(key, secret, paper=True)
        broker = AlpacaPaperBroker(client)
        positions = {
            position.symbol: {
                "qty": int(abs(float(position.qty))),
                "entry_price": float(position.avg_entry_price),
            }
            for position in client.get_all_positions()
            if float(position.qty) != 0
        }
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        # The default query returns OPEN orders only: a filled buy from last night would be
        # invisible and the position adopted with no clock. Ask for ALL and filter.
        orders = client.get_orders(GetOrdersRequest(status=QueryOrderStatus.ALL))
        fills: dict[str, dict] = {}
        for order in orders:
            if _order_status(_attr(order, "side", "")) != "buy":
                continue
            if _order_status(_attr(order, "status", "")) != "filled":
                continue
            filled_at = _attr(order, "filled_at", None)
            if filled_at is None:
                continue
            stamp = ts_us_of(filled_at)
            if _et_day_of(stamp) != day:
                continue  # only TODAY's fills describe today's positions
            known = fills.get(order.symbol)
            if known is not None and known["filled_at_us"] >= stamp:
                continue
            fills[order.symbol] = {
                "entry_minute": et_minute_of(stamp),
                "entry_price": float(_attr(order, "filled_avg_price", 0) or 0),
                "client_order_id": str(_attr(order, "client_order_id", "")),
                "filled_at_us": stamp,
            }
        open_positions = reconcile_positions(journal_file, positions, fills)
        for order in orders:
            client_id = str(getattr(order, "client_order_id", ""))
            if client_id.startswith("depthbot-"):
                append_event(
                    journal_file,
                    {
                        "event": "reconcile_order",
                        "ticker": order.symbol,
                        "client_order_id": client_id,
                        "status": str(order.status),
                    },
                )
    model = model or depth_bot_features.load_model()
    if resume_us is None and history is not None:
        resume_us = restart_clock(
            journal_file,
            int(time.time() * 1_000_000),
            minute_us(day, LATTICE_START - 5),
            minute_us(day, session_end),
        )
    if universe is None and getattr(source, "bars", None) is not None:
        # A replay-backed source still declares the universe it swept.
        universe = sorted(set(source.bars["ticker"].to_list()))
    quotes = QuoteCache(day)
    if mode == "shadow":
        engine_broker = SimBroker(day, quotes, minute_us(day, session_end))
    else:
        if broker is None:
            raise RuntimeError("paper mode requires an Alpaca paper broker")
        engine_broker = broker
    engine = DepthEngine(
        day,
        session_end,
        model,
        engine_broker,
        journal_file,
        open_positions=open_positions,
        fresh=False,
        quotes=quotes,
        kill_path=kill_file,
        alert_path=alerts,
        history=history,
        universe=universe,
        states_path=states_path_for(journal_root, states_root, day),
        # The journal records the BROKER mode (sim vs paper), so replay and shadow stay
        # byte-identical; the run mode is carried by live_session_complete.
        mode=str(getattr(engine_broker, "mode", mode)),
    )
    if resume_us is not None:
        engine.bootstrap(resume_us=int(resume_us))
    source.run(engine.on_bar, engine.on_quote)
    reason = getattr(source, "stop_reason", None)
    if reason not in (None, "session_end"):
        # The feed died and could not be revived: never write a completed empty day.
        engine.write_states()
        engine._alert(
            "session_incomplete",
            reason=reason,
            bars=len(engine.bar_rows),
            decisions=len(engine.decision_ticks),
        )
        append_event(
            journal_file,
            {
                "event": "session_incomplete",
                "day": day,
                "mode": mode,
                "reason": reason,
                "bars": len(engine.bar_rows),
                "decisions": len(engine.decision_ticks),
            },
        )
        raise StreamEndedEarlyError(f"{day} session ended early: {reason}")
    daily, trades = engine.finish()
    write_capture(
        {"bars": pl.DataFrame(engine.bar_rows) if engine.bar_rows else None,
         "quote_cache": engine.quotes},
        day,
        journal_root,
    )
    append_event(
        journal_file,
        {"event": "live_session_complete", "day": day, "mode": mode,
         "attempts": daily["attempts"], "trade_count": len(trades)},
    )
    return daily, trades


def restart_clock(
    journal_file: Path, now_us: int, start_us: int, end_us: int
) -> int | None:
    """Clock a crashed session must be rebuilt to, or ``None`` for a clean start."""
    opened = closed = False
    for event in read_events(journal_file):
        if event.get("event") == "session_start":
            opened, closed = True, False
        elif event.get("event") == "session_end":
            closed = True
    if not opened or closed:
        return None
    if not (int(start_us) < int(now_us) < int(end_us)):
        return None
    return int(now_us)


def supervise(
    run_session: Callable[[], Any],
    *,
    kill_path: Path | None = None,
    alert_path: Path | None = None,
    sleep: Callable[[float], None] = time.sleep,
    backoff_s: float = 5.0,
    max_backoff_s: float = 300.0,
    max_restarts: int | None = 5,
    max_sessions: int | None = None,
    day_fn: Callable[[], str] | None = None,
    wait_next_day: bool = True,
    day_poll_s: float = 60.0,
) -> int:
    """Restart on crash with exponential backoff, one session per ET day, stop on data/KILL."""
    restarts = 0
    sessions = 0
    kill_path = Path(kill_path) if kill_path is not None else KILL_PATH
    day_fn = day_fn or (lambda: datetime.now(ET).date().isoformat())
    while not kill_active(kill_path):
        if max_sessions is not None and sessions >= max_sessions:
            break
        session_day = day_fn()
        try:
            run_session()
        except KeyboardInterrupt:
            break
        except Exception as exc:  # noqa: BLE001 - a crash is an alert, never a silent exit
            restarts += 1
            write_alert(
                alert_path,
                "crash",
                reason=type(exc).__name__,
                detail=str(exc),
                restarts=restarts,
                kill=kills_left(kill_path),
            )
            if max_restarts is not None and restarts > max_restarts:
                write_alert(alert_path, "supervisor_giving_up", restarts=restarts)
                return 2
            # Never a tight restart loop: the wait doubles per consecutive crash.
            sleep(min(float(max_backoff_s), backoff_s * (2 ** (restarts - 1))))
            continue
        restarts = 0  # a completed session resets the crash ladder
        sessions += 1
        if max_sessions is not None and sessions >= max_sessions:
            break
        if wait_next_day:
            while not kill_active(kill_path) and day_fn() == session_day:
                sleep(day_poll_s)
        else:
            sleep(backoff_s)
    if kill_active(kill_path):
        write_alert(alert_path, "kill_stop", kill_path=str(kill_path))
    return 0


def kills_left(kill_path: Path) -> bool:
    return kill_active(kill_path)



# ----- first-shadow-day journal checklist -------------------------------------
SKIP_VOCABULARY = set(research.SKIP_REASONS) | {
    "kill_stop",
    "daily_loss_stop",
    "flatten_time",
    "universe_refused",
}
CHECKLIST_COMPARE_FIELDS = (
    "ticker",
    "t",
    "entry_minute",
    "intent_us",
    "limit_price",
    "qty",
    "qty_capital",
    "entry_status",
    "entry_ask",
    "entry_bid",
    "entry_age_s",
    "entry_quote_us",
    "entry_arrival_us",
    "exit_minute",
    "exit_bid",
    "exit_bid_shares",
    "exit_status",
    "actual_exit_us",
    "fill_status",
    "net_usd_25",
    "attempt_index",
    "positions_at_entry",
)


def _captured_frames(bot_root: Path, day: str) -> tuple[pl.DataFrame | None, pl.DataFrame | None]:
    bars_path = Path(bot_root) / "bars" / f"{day}.parquet"
    bars = pl.read_parquet(bars_path) if bars_path.is_file() else None
    quote_dir = Path(bot_root) / "quotes"
    frames = []
    if quote_dir.is_dir():
        for symbol_dir in sorted(quote_dir.iterdir()):
            path = symbol_dir / f"{day}.parquet"
            if path.is_file():
                frames.append(pl.read_parquet(path))
    quotes = pl.concat(frames) if frames else None
    return bars, quotes


def build_checklist(
    day: str,
    *,
    journal_root: Path = JOURNAL_ROOT,
    bot_root: Path | None = None,
    states_root: Path | None = None,
    data_root: Path = DATA_ROOT,
    require_live: bool = True,
    compare_replay: bool = True,
) -> list[dict[str, Any]]:
    """Evaluate the reviewer's first-shadow-day checklist from the artefacts of one day."""
    journal_root = Path(journal_root)
    bot_root = Path(bot_root) if bot_root is not None else journal_root.parent
    states_file = states_path_for(journal_root, states_root, day)
    journal_file = journal_file_for(journal_root, day)
    recorded = list(read_events(journal_file))
    kinds = [event.get("event") for event in recorded]
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"item": name, "ok": bool(ok), "detail": detail})

    summary = next((event for event in recorded if event.get("event") == "daily_summary"), {})
    session_end = int(summary.get("session_end") or session_end_for_day(day))
    lattice = tuple(range(LATTICE_START, min(ENTRY_CUTOFF_MINUTE, session_end - 1) + 1, 5))

    # 1. lifecycle
    required = {"session_start", "session_end", "daily_summary"}
    if require_live:
        required.add("live_session_complete")
    alerts = [
        event.get("kind")
        for event in recorded
        if event.get("event") == "ALERT"
    ]
    fatal_alerts = {
        "stream_ended_early",
        "stream_reconnect_exhausted",
        "crash",
        "session_incomplete",
    } & set(alerts)
    add(
        "1 complete lifecycle (start, end, summary, session marker)",
        required <= set(kinds) and "session_incomplete" not in kinds and not fatal_alerts,
        f"missing={sorted(required - set(kinds))} fatal={sorted(fatal_alerts)}",
    )

    # 2. snapshot coverage
    coverage = {
        int(event["t"]): event
        for event in recorded
        if event.get("event") == "snapshot_coverage"
    }
    expected_snapshots = [
        snapshot
        for snapshot in depth_bot_features.SNAPSHOTS
        if snapshot in lattice
    ]
    thin = [
        snapshot
        for snapshot in expected_snapshots
        if snapshot not in coverage
        or coverage[snapshot].get("ratio") is None
        or float(coverage[snapshot]["ratio"]) < 0.5
    ]
    refusals = sum(event == "universe_refused" for event in kinds)
    backfills = sum(
        event == "ALERT" and recorded[index].get("kind") == "backfill_failed"
        for index, event in enumerate(kinds)
    )
    add(
        "2 snapshot coverage >=50% of the declared universe at every snapshot",
        not thin and refusals == 0 and backfills <= 1,
        f"thin={thin} refusals={refusals} backfill_failed={backfills}",
    )

    # 3. decision lattice and skip vocabulary
    ticks = [int(event["t"]) for event in recorded if event.get("event") == "decision_tick"]
    latticed = len(set(ticks) & set(lattice))
    intents = [event for event in recorded if event.get("event") == "intent"]
    reasons = [event.get("skip_reason") for event in intents if event.get("skip_reason")]
    unknown = sorted(set(reasons) - SKIP_VOCABULARY)
    quote_skips = sum(
        reason in {"quote_not_acquired", "intent_not_firm_fresh"} for reason in reasons
    )
    share = quote_skips / len(intents) if intents else 0.0
    add(
        "3 lattice ticks present, skip reasons in the research vocabulary",
        len(lattice) > 0
        and latticed >= 0.95 * len(lattice)
        and not unknown
        and share <= 0.5,
        f"lattice={latticed}/{len(lattice)} unknown={unknown} quote_skip_share={share:.2f}",
    )

    # 4. persisted artefacts
    bars_path = bot_root / "bars" / f"{day}.parquet"
    columns_ok = False
    if states_file.is_file():
        states = pl.read_parquet(states_file)
        columns_ok = tuple(states.columns) == tuple(research.STATE_COLUMNS)
    quoted = {
        event["ticker"]
        for event in recorded
        if event.get("event") == "intent"
        and (event.get("intent_quote") or {}).get("intent_status") == "quoted"
    }
    missing_quotes = [
        ticker
        for ticker in sorted(quoted)
        if not (bot_root / "quotes" / ticker / f"{day}.parquet").is_file()
    ]
    add(
        "4 bars/states/quotes persisted (states schema = research.STATE_COLUMNS)",
        bars_path.is_file() and states_file.is_file() and columns_ok and not missing_quotes,
        f"bars={bars_path.is_file()} states={states_file.is_file()} schema={columns_ok} "
        f"missing_quotes={missing_quotes[:5]}",
    )

    # 5. shadow journal vs a replay of the captured day
    if compare_replay:
        bars, quotes = _captured_frames(bot_root, day)
        if bars is None or quotes is None:
            add("5 shadow == replay on the captured tape", False, "captures missing")
        else:
            scratch = Path(tempfile.mkdtemp(prefix="depthbot-checklist-"))
            replay_journal = journal_file_for(scratch / "journal", day)
            try:
                replay_day(
                    day,
                    research.OUTPUT,
                    data_root,
                    scratch / "journal",
                    source=ReplayStreamSource(bars, quotes, day),
                )
            except Exception as exc:  # noqa: BLE001 - a broken capture is a checklist failure
                add("5 shadow == replay on the captured tape", False, f"replay failed: {exc}")
            else:
                reference = list(read_events(replay_journal))
                same = _compare_journals(recorded, reference)
                stops = sum(event == "risk_stop" for event in kinds)
                add(
                    "5 shadow == replay on the captured tape ($300 stop fires 0)",
                    same[0] and stops == 0,
                    f"{same[1]} stop_fires={stops}",
                )
    else:
        add("5 shadow == replay on the captured tape", True, "skipped (--no-replay)")

    # 6. no orders, ARMED absent, one session
    alert_file = bot_root / "ALERT"
    alert_lines = (
        [line for line in alert_file.read_text().splitlines() if line.strip()]
        if alert_file.is_file()
        else []
    )
    modes = {
        event.get("mode")
        for event in recorded
        if event.get("event") in {"session_start", "daily_summary"} and event.get("mode")
    }
    starts = sum(event == "session_start" for event in kinds)
    add(
        "6 no paper orders, no alert, ARMED absent, one session",
        not alert_lines
        and modes <= {"sim"}
        and not ARMED_PATH.is_file()
        and starts == 1,
        f"alerts={len(alert_lines)} modes={sorted(modes)} starts={starts} "
        f"armed={ARMED_PATH.is_file()}",
    )
    return checks


def _compare_journals(observed: list[dict], reference: list[dict]) -> tuple[bool, str]:
    def pick(events_list: list[dict]) -> dict[str, list]:
        ticks = [
            (event["t"], event["candidate_count"], event["signal_count"])
            for event in events_list
            if event.get("event") == "decision_tick"
        ]
        signals = [
            (event["ticker"], event["t"], event["intent_minute"], event["pred_60"])
            for event in events_list
            if event.get("event") == "signal"
        ]
        intents = [
            (event["ticker"], event["intent_us"], event["qty"], event.get("skip_reason"))
            for event in events_list
            if event.get("event") == "intent"
        ]
        trades = [
            tuple(event.get(field) for field in CHECKLIST_COMPARE_FIELDS)
            for event in events_list
            if event.get("event") == "trade_result"
        ]
        return {
            "decision_ticks": ticks,
            "signals": signals,
            "intents": intents,
            "trades": trades,
        }

    left, right = pick(observed), pick(reference)
    for name in left:
        if left[name] != right[name]:
            return False, f"{name} differ ({len(left[name])} vs {len(right[name])})"
    return True, "ticks/signals/intents/trades identical"


def run_checklist(args: argparse.Namespace) -> int:
    checks = build_checklist(
        args.day,
        journal_root=args.journal_root,
        bot_root=args.bot_root,
        states_root=args.states_root,
        compare_replay=not args.no_replay,
    )
    for check in checks:
        status = "PASS" if check["ok"] else "FAIL"
        print(f"{status}  {check['item']}  [{check['detail']}]")
    failed = [check for check in checks if not check["ok"]]
    print(f"{len(checks) - len(failed)}/{len(checks)} checks pass for {args.day}")
    return 0 if not failed else 1


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="mode")
    parser.set_defaults(mode="shadow")
    replay = subs.add_parser("replay", help="run a stored SIP day through the live engine")
    replay.add_argument("--day", required=True)
    replay.add_argument("--state-root", type=Path, default=research.OUTPUT)
    replay.add_argument("--data-root", type=Path, default=DATA_ROOT)
    replay.add_argument("--journal-root", type=Path, default=JOURNAL_ROOT)
    for name in ("shadow", "paper"):
        live = subs.add_parser(name)
        live.add_argument("--day")
        live.add_argument("--paper", action="store_true", help="explicit paper-order opt-in")
        live.add_argument(
            "--kill-file",
            type=Path,
            default=None,
            help="kill switch (default data/forward/depth_bot/KILL)",
        )
    boss = subs.add_parser("supervise", help="restart the session on crash, stop on data/KILL")
    boss.add_argument("--live-mode", choices=("shadow", "paper"), default="shadow")
    boss.add_argument("--paper", action="store_true", help="explicit paper-order opt-in")
    boss.add_argument("--backoff", type=float, default=30.0)
    boss.add_argument(
        "--kill-file",
        type=Path,
        default=None,
        help="kill switch (default data/forward/depth_bot/KILL)",
    )
    audit = subs.add_parser("checklist", help="evaluate the first-shadow-day journal checklist")
    audit.add_argument("--day", required=True)
    audit.add_argument("--journal-root", type=Path, default=JOURNAL_ROOT)
    audit.add_argument("--bot-root", type=Path, default=None)
    audit.add_argument("--states-root", type=Path, default=None)
    audit.add_argument("--no-replay", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    if args.mode == "replay":
        daily, trades = replay_day(
            args.day, args.state_root, args.data_root, args.journal_root
        )
        print(
            json.dumps(
                {
                    "day": args.day,
                    "arm_key": ARM.key,
                    "attempts": daily["attempts"],
                    "trades": len(trades),
                    "known_fills": daily["known_fills"],
                }
            )
        )
        return 0
    if args.mode == "checklist":
        return run_checklist(args)
    if args.mode == "supervise":
        return supervise(
            lambda: run_live(
                args.live_mode,
                args.paper,
                journal_root=JOURNAL_ROOT,
                kill_path=args.kill_file,
            ),
            kill_path=args.kill_file,
            alert_path=JOURNAL_ROOT.parent / "ALERT",
            backoff_s=float(args.backoff),
        )
    run_live(args.mode, args.paper, args.day, kill_path=args.kill_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
