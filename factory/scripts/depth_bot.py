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
"""
from __future__ import annotations

import argparse
import heapq
import json
import os
import sys
from collections import Counter
from collections.abc import Iterator
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
KILL_PATH = DATA_ROOT / "KILL"

ARM = research.View(0.030, 0.50)
LOSS_LIMIT_USD = 90.0
LATTICE_START = 575
ENTRY_CUTOFF_MINUTE = 895  # research: the last decision lattice t with an entry
FLATTEN_MINUTE = 955  # ops: hard flatten at 15:55 ET
BAR_SETTLE_US = 2_000_000  # a minute is closed only after its own bar can have arrived
MAX_ORDER_NOTIONAL = 1_100.0  # ops sanity guard; the frozen sizing never reaches it

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


def ts_us_of(value: Any) -> int:
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, datetime):
        return int(value.timestamp() * 1_000_000)
    raise TypeError(f"unsupported quote clock: {type(value).__name__}")


def et_minute_of(us: int) -> int:
    clock = datetime.fromtimestamp(int(us) / 1_000_000, tz=UTC).astimezone(ET)
    return clock.hour * 60 + clock.minute


def _attr(value: Any, name: str, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _order_status(value: Any) -> str:
    return str(getattr(value, "value", value)).lower()


# ----- operational controls ---------------------------------------------------
def kill_active(kill_path: Path = KILL_PATH) -> bool:
    return Path(kill_path).exists()


def entry_control_allowed(kill_path: Path = KILL_PATH, exit_order: bool = False) -> bool:
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

    Stores only the prints actually observed at/after a ticker's admission and builds the
    same ``SymbolQuotes`` arrays ``alpha_quote_aware_frequency.symbol_quotes`` would build
    from the full day, so ``latest_raw``/``first_eligible_at_or_after`` answer identically
    on every prefix of the day.
    """

    def __init__(self, day: str):
        self.day = day
        self.size_multiplier = 100 if day < UNIT_EPOCH else 1
        self.raw: dict[str, list[dict]] = {}
        self._stamps: dict[str, list[int]] = {}
        self._bid: dict[str, list[float]] = {}
        self._ask: dict[str, list[float]] = {}
        self._bid_size: dict[str, list[float]] = {}
        self._ask_size: dict[str, list[float]] = {}
        self._regular: dict[str, list[bool]] = {}
        self._streams: dict[str, SymbolQuotes] = {}
        self._dirty: set[str] = set()

    def add(self, quote: dict) -> None:
        symbol = str(quote["symbol"])
        conditions = [str(c) for c in (quote.get("conditions") or [])]
        regular = bool(conditions) and all(condition == "R" for condition in conditions)
        stamp = ts_us_of(quote["ts_utc"])
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
        self._stamps.setdefault(symbol, []).append(stamp)
        self._bid.setdefault(symbol, []).append(float(quote.get("bid_price") or 0.0))
        self._ask.setdefault(symbol, []).append(float(quote.get("ask_price") or 0.0))
        self._bid_size.setdefault(symbol, []).append(float(quote.get("bid_size") or 0.0))
        self._ask_size.setdefault(symbol, []).append(float(quote.get("ask_size") or 0.0))
        self._regular.setdefault(symbol, []).append(regular)
        self._dirty.add(symbol)

    def stream(self, ticker: str) -> SymbolQuotes | None:
        if ticker not in self._stamps:
            return None
        if ticker in self._dirty:
            bid = np.asarray(self._bid[ticker], dtype=float)
            ask = np.asarray(self._ask[ticker], dtype=float)
            bid_shares = np.asarray(self._bid_size[ticker], dtype=float) * self.size_multiplier
            ask_shares = np.asarray(self._ask_size[ticker], dtype=float) * self.size_multiplier
            valid = (
                np.isfinite(bid)
                & np.isfinite(ask)
                & np.isfinite(bid_shares)
                & np.isfinite(ask_shares)
                & (bid > 0)
                & (ask > 0)
                & (bid <= ask)
            )
            self._streams[ticker] = SymbolQuotes(
                ticker=ticker,
                stamps=np.asarray(self._stamps[ticker], dtype=np.int64),
                bid=bid,
                ask=ask,
                bid_shares=bid_shares,
                ask_shares=ask_shares,
                regular=np.asarray(self._regular[ticker], dtype=bool),
                valid=valid,
            )
            self._dirty.discard(ticker)
        return self._streams.get(ticker)

    def rows(self) -> list[dict]:
        return [row for ticker in sorted(self.raw) for row in self.raw[ticker]]


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

    def __init__(self, positions: dict[str, int] | None = None):
        self.positions = dict(positions or {})
        self.submitted: list[dict] = []

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
        return {
            "state": "closed",
            "exit_status": "conditional_market_fill",
            "exit_submit_us": int(order["submit_us"]),
            "exit_arrival_us": int(order["submit_us"]),
            "actual_exit_us": int(order["submit_us"]) + 60_000,
            "broker_order_id": order["client_order_id"],
            "filled_qty": int(order["qty"]),
            "filled_avg_price": 0.0,
        }

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
            return {
                "state": UNKNOWN,
                "exit_status": f"exit_{status}",
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
        return {
            "state": UNKNOWN,
            "exit_status": "exit_order_still_working",
            "exit_submit_us": int(_attr(order, "submit_us", session_end_us)),
            "actual_exit_us": int(session_end_us) + 1,
            "broker_status": _order_status(_attr(current, "status", "unknown")),
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
        "broker_fill_price": None,
        "broker_filled_qty": None,
        "partial_entry": False,
        "late_exit": False,
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
        self.bar_rows: list[dict] = []
        self.admitted: set[str] = set()
        self.decision_ticks: list[dict] = []
        self.trades: list[dict] = []
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
            "skip_reasons": Counter(),
            "_position_samples": [],
        }
        if fresh:
            self.journal_file.parent.mkdir(parents=True, exist_ok=True)
            self.journal_file.write_text("", encoding="utf-8")
        else:
            self._recover_from_journal()
        for ticker, saved in (open_positions or {}).items():
            self._adopt_position(ticker, saved)
        if self.flatten_enabled:
            self._schedule(
                minute_us(day, min(FLATTEN_MINUTE, self.session_end)),
                FLATTEN_PRIORITY,
                "flatten",
                None,
            )

    # -- journal / recovery ----------------------------------------------------
    def _log(self, event: dict) -> None:
        append_event(self.journal_file, event)

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
        if not position.record:
            position.record = {
                "day": self.day,
                "ticker": ticker,
                "qty": quantity,
                "entry_minute": entry_minute,
                "exit_minute": exit_minute,
                "entry_price": position.entry_price,
                "entry_ask": position.entry_price,
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
        self._poll_orders()
        self._drain_tasks()

    # -- stream callbacks ------------------------------------------------------
    def on_bar(self, bars: pl.DataFrame) -> set[str]:
        before = set(self.features.admissions)
        rows = (
            bars.select("ticker", "et", "open", "high", "low", "close", "volume")
            .sort(["et", "ticker"])
            .to_dicts()
        )
        for row in rows:
            minute = int(row["et"])
            self.advance(minute_us(self.day, minute + 1))
            self._bar_buffer.setdefault(minute, []).append(row)
        return set(self.features.admissions) - before

    def on_quote(self, quote: dict) -> None:
        symbol = str(quote["symbol"])
        admitted = symbol in self.admitted
        if admitted:
            self.quotes.add(quote)
        self.advance(ts_us_of(quote["ts_utc"]))

    # -- minute/decision pipeline ---------------------------------------------
    def _close_minutes(self) -> None:
        ready = [
            minute
            for minute in self._bar_buffer
            if minute_us(self.day, minute + 1) + BAR_SETTLE_US <= self.now_us
        ]
        for minute in sorted(ready):
            self._flush_minute(minute)

    def _flush_minute(self, minute: int) -> None:
        rows = self._bar_buffer.pop(minute, [])
        if not rows:
            return
        self.features.add_bars(pl.DataFrame(rows))
        self.bar_rows.extend(rows)
        self.admitted.update(self.features.admissions)
        t = minute + 1
        if t % 5 or not LATTICE_START <= t <= min(ENTRY_CUTOFF_MINUTE, self.session_end - 1):
            return
        scored = self.features.decision(t)
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
    def _poll_orders(self) -> None:
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
        if outcome.get("filled_qty") is not None:
            record["broker_filled_qty"] = int(outcome["filled_qty"])
        if outcome.get("partial"):
            record["partial_entry"] = True
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
        if int(self.now_us) < int(position.due_us) and not position.forced:
            return
        submit_us = int(position.due_us)
        status: str | None = None
        if position.forced:
            submit_us = int(self.now_us)
            status = "flatten_forced"
        else:
            stream = self.quotes.stream(position.ticker)
            if stream is None:
                return
            quote, _why = stream.latest_raw(submit_us)
            if quote is not None and float(quote["age_s"]) <= MAX_AGE_S:
                status = "submit_at_due_intent_firm_fresh"
            else:
                rested, _why = stream.first_eligible_at_or_after(submit_us, self.now_us)
                if rested is None:
                    return
                submit_us = int(rested["quote_us"])
                status = "submit_at_first_regular_after_due_intent"
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
        if outcome["state"] == "closed":
            quantity = int(outcome.get("filled_qty") or position.qty)
            broker_price = outcome.get("filled_avg_price")
            entry_price = (
                position.entry_price if position.entry_price is not None else record["entry_ask"]
            )
            exit_price = broker_price if broker_price is not None else record["exit_bid"]
            realized = (
                quantity * (float(exit_price) - float(entry_price))
                if exit_price is not None and entry_price is not None
                else 0.0
            )
            record["fill_status"] = "conditional_fill"
            if record["exit_bid"] is not None and record["entry_ask"] is not None:
                for rung in research.RUNG_COSTS:
                    record[f"net_usd_{int(rung)}"] = research.net_usd(
                        quantity, float(record["exit_bid"]), float(record["entry_ask"]), rung
                    )
            record["broker_fill_price"] = None if broker_price is None else float(broker_price)
            record["broker_filled_qty"] = (
                None if outcome.get("filled_qty") is None else quantity
            )
            record["realized_pnl_usd"] = realized
            self.daily["fills"] += 1
            self.daily["known_fills"] += 1
        else:
            record["fill_status"] = "unknown_exit_execution"
            realized = 0.0
            self.daily["unknown_fills"] += 1
        actual_exit_us = int(record["actual_exit_us"] or self.session_end_us + 1)
        self._close_position(position, realized, outcome, actual_exit_us)

    def _close_position(
        self, position: Position, realized: float, outcome: dict, actual_exit_us: int
    ) -> None:
        record = position.record
        record["actual_exit_us"] = int(actual_exit_us)
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
                "qty": position.qty,
                "exit_bid": record.get("exit_bid"),
                "broker_exit_price": record.get("broker_fill_price"),
                "exit_slippage": (
                    float(record["broker_fill_price"]) - float(record["exit_bid"])
                    if record.get("broker_fill_price") and record.get("exit_bid")
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
        self.trades.append(record)
        self._log({"event": "trade_result", **record})

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
            elif kind == "flatten":
                self._flatten()

    # -- shutdown --------------------------------------------------------------
    def finish(self) -> tuple[dict, list[dict]]:
        self.now_us = max(self.now_us, self.session_end_us)
        for minute in sorted(self._bar_buffer):
            self._flush_minute(minute)
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
        self._drain_tasks()
        samples = self.daily.pop("_position_samples", [])
        self.daily["positions_peak"] = max(samples) if samples else 0
        self.daily["positions_mean"] = float(sum(samples) / len(samples)) if samples else 0.0
        self.daily["trade_count"] = len(self.trades)
        self.daily["mode"] = getattr(self.broker, "mode", "unknown")
        summary = dict(self.daily)
        summary["skip_reasons"] = dict(self.daily["skip_reasons"])
        self._log({"event": "daily_summary", **summary})
        if self.positions:  # pragma: no cover - only reachable on a broker failure
            self._log(
                {
                    "event": "ALERT",
                    "day": self.day,
                    "reason": "position_not_flattened",
                    "tickers": sorted(self.positions),
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

    def _quote_events(self) -> list[dict]:
        if self.quotes is None or not self.quotes.height:
            return []
        frame = self.quotes.with_columns(pl.col("ts_utc").cast(pl.Int64).alias("ts_us"))
        return frame.sort(["ts_us", "symbol"]).to_dicts()

    def run(self, on_bar, on_quote) -> None:
        bar_events = self._bar_events()
        quote_events = self._quote_events()
        bar_i = quote_i = 0
        while bar_i < len(bar_events) or quote_i < len(quote_events):
            bar_clock = bar_events[bar_i][0] if bar_i < len(bar_events) else None
            quote_row = quote_events[quote_i] if quote_i < len(quote_events) else None
            quote_clock = int(quote_row["ts_us"]) if quote_row is not None else None
            if bar_clock is not None and (quote_clock is None or bar_clock <= quote_clock):
                on_bar(bar_events[bar_i][1])
                bar_i += 1
            else:
                on_quote(quote_row)
                quote_i += 1


class AlpacaSIPStream:
    """Live Alpaca SIP stream: all-symbol minute bars, quotes for admitted names."""

    def __init__(self, key: str, secret: str, universe: list[str]):
        self.key = key
        self.secret = secret
        self.universe = list(universe)

    def run(self, on_bar, on_quote) -> None:
        from alpaca.data.enums import DataFeed
        from alpaca.data.live import StockDataStream

        stream = StockDataStream(self.key, self.secret, feed=DataFeed.SIP)
        subscribed: set[str] = set()

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
            newly = on_bar(pl.DataFrame([row]))
            names = sorted(set(newly or ()) - subscribed)
            if names:
                stream.subscribe_quotes(quote_handler, *names)
                subscribed.update(names)

        async def quote_handler(quote):
            on_quote(
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

        stream.subscribe_bars(bar_handler, *self.universe)
        stream.run()


# ----- reference data ---------------------------------------------------------
def load_pit_universe(day: str, data_root: Path = DATA_ROOT) -> list[str]:
    path = Path(data_root) / "pit" / "pit_symbols.parquet"
    if not path.is_file():
        raise FileNotFoundError(f"PIT symbol universe missing: {path}")
    table = pl.read_parquet(path, columns=["vintage", "symbol"])
    eligible = table.filter(pl.col("vintage") <= day)
    if eligible.is_empty():
        raise ValueError(f"no PIT universe vintage exists on or before {day}")
    latest = eligible["vintage"].max()
    return sorted(set(eligible.filter(pl.col("vintage") == latest)["symbol"].to_list()))


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
    quotes = capture.get("quotes")
    if quotes:
        frame = pl.DataFrame(quotes)
        for (ticker,), part in frame.partition_by("symbol", as_dict=True).items():
            quote_dir = root / "quotes" / str(ticker)
            quote_dir.mkdir(parents=True, exist_ok=True)
            part.write_parquet(quote_dir / f"{day}.parquet")


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
    )
    source.run(engine.on_bar, engine.on_quote)
    daily, trades = engine.finish()
    write_capture(
        {"bars": pl.DataFrame(engine.bar_rows) if engine.bar_rows else None,
         "quotes": engine.quotes.rows()},
        day,
        journal_root,
        data_root,
    )
    return daily, trades


def reconcile_positions(
    journal_file: Path, broker_positions: dict[str, dict]
) -> dict[str, dict]:
    """Adopt broker positions and re-derive their exit clocks from the journal."""
    saved = {}
    for event in read_events(journal_file):
        if event.get("event") in {"position_open", "fill"}:
            saved[event["ticker"]] = event
        elif event.get("event") == "position_closed":
            saved.pop(event.get("ticker"), None)
    adopted: dict[str, dict] = {}
    for ticker, position in broker_positions.items():
        prior = saved.get(ticker, {})
        adopted[ticker] = {
            "qty": int(position.get("qty", 0)),
            "entry_price": float(position.get("entry_price", 0.0)),
            "entry_minute": int(prior.get("entry_minute", position.get("entry_minute", 0))),
            "exit_minute": int(prior.get("exit_minute", 0)),
            "reserved_usd": float(
                prior.get(
                    "reserved_usd",
                    float(position.get("qty", 0))
                    * float(position.get("entry_price", 0.0))
                    * research.FUNDING_SIDE_FACTOR,
                )
            ),
            "client_order_id": prior.get("client_order_id"),
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
) -> tuple[dict, list[dict]]:
    """Run one session: shadow never orders, paper requires --paper AND the ARMED file."""
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
    key = secret = None
    if source is None:
        key, secret = _load_credentials()
        source = AlpacaSIPStream(key, secret, load_pit_universe(day))
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
        open_positions = reconcile_positions(journal_file, positions)
        for order in client.get_orders():
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
        kill_path=KILL_PATH,
    )
    source.run(engine.on_bar, engine.on_quote)
    daily, trades = engine.finish()
    write_capture(
        {"bars": pl.DataFrame(engine.bar_rows) if engine.bar_rows else None,
         "quotes": engine.quotes.rows()},
        day,
        journal_root,
    )
    append_event(
        journal_file,
        {"event": "live_session_complete", "day": day, "mode": mode,
         "attempts": daily["attempts"], "trade_count": len(trades)},
    )
    return daily, trades


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
    run_live(args.mode, args.paper, args.day)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
