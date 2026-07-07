"""
Phase 7 — Paper execution gateway and reconciliation per SPEC §14-15.

Provides a clean, mockable execution interface for paper-mode order
submission and position reconciliation.  No actual broker calls —
subclasses or adapters wire real Alpaca later.

Reconciliation handles the 8 restart cases from SPEC §15.4.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import TYPE_CHECKING, Optional
from zoneinfo import ZoneInfo

from loguru import logger

if TYPE_CHECKING:
    from src.trade_ledger import TradeLedger

from src.models.schemas import (
    EntrySignal,
    OrderActionType,
    PendingOrder,
    PositionState,
    PositionStateModel,
)
from src.state_machine import (
    PositionStore,
    PendingOrderStore,
    is_valid_transition,
    transition_position,
)


# ──────────────────────────────────────────────────────────────────
#  Market session (SPEC §11.7 — live-readiness)
# ──────────────────────────────────────────────────────────────────


@dataclass
class MarketSession:
    """Result of an Alpaca calendar/clock check.

    ``is_open`` is the trading session status.
    ``source`` identifies how the session was determined.
    ``open_time`` / ``close_time`` are market hours (ET).
    ``flatten_time`` is the time at which positions should be flattened
    (close - 5 min).
    """

    is_open: bool = False
    source: str = "fallback_hardcoded"
    open_time: Optional[time] = None
    close_time: Optional[time] = None
    flatten_time: Optional[time] = None
    next_open: Optional[datetime] = None
    next_close: Optional[datetime] = None


# US federal holidays 2026 — used when Alpaca API is unavailable.
US_HOLIDAYS_2026: set[date] = {
    date(2026, 1, 1),    # New Year's Day
    date(2026, 1, 19),   # Martin Luther King Jr. Day
    date(2026, 2, 16),   # Presidents' Day
    date(2026, 4, 3),    # Good Friday
    date(2026, 5, 25),   # Memorial Day
    date(2026, 6, 19),   # Juneteenth
    date(2026, 7, 3),    # Independence Day (observed)
    date(2026, 9, 7),    # Labor Day
    date(2026, 11, 26),  # Thanksgiving
    date(2026, 12, 25),  # Christmas
}


def get_alpaca_market_session(
    gateway: "AlpacaExecutionGateway",
    now_et: Optional[datetime] = None,
) -> MarketSession:
    """Fetch market session from Alpaca calendar/clock API.

    SPEC §11.7: calendar entry → session; no calendar entry → closed
    (holiday/weekend). Falls back to US_HOLIDAYS_2026 / hardcoded
    weekend / 9:30-16:00 when the API is unavailable.

    Half-day early close: flatten_time = close_time - 5 min.
    """
    if now_et is None:
        now_et = datetime.now(ZoneInfo("US/Eastern"))

    today = now_et.date()

    try:
        from alpaca.trading.requests import GetCalendarRequest

        client = gateway.client
        calendar = client.get_calendar(GetCalendarRequest(start=today, end=today))

        if not calendar:
            # No calendar entry → holiday or non-trading day
            return MarketSession(
                is_open=False,
                source="alpaca_calendar_closed",
                open_time=None,
                close_time=None,
                flatten_time=None,
            )

        entry = calendar[0]
        open_str: str = str(entry.open) if hasattr(entry, "open") else "09:30"
        close_str: str = str(entry.close) if hasattr(entry, "close") else "16:00"

        h_open, m_open = open_str.split(":") if ":" in open_str else ("09", "30")
        h_close, m_close = close_str.split(":") if ":" in close_str else ("16", "00")
        open_time = time(int(h_open), int(m_open))
        close_time = time(int(h_close), int(m_close))

        # Build flatten_time = close - 5 min
        close_dt = datetime.combine(today, close_time)
        flatten_dt = close_dt - timedelta(minutes=5)
        flatten_time = flatten_dt.time()

        # Check clock for is_open
        try:
            clock = client.get_clock()
            is_open = bool(clock.is_open) if hasattr(clock, "is_open") else False
            next_open = (
                clock.next_open if hasattr(clock, "next_open") else None
            )
            next_close = (
                clock.next_close if hasattr(clock, "next_close") else None
            )
        except Exception:
            # Fallback: compare now against open/close times
            now_t = now_et.time()
            is_open = now_et.weekday() < 5 and open_time <= now_t < close_time
            next_open = None
            next_close = None

        return MarketSession(
            is_open=is_open,
            source="alpaca_calendar",
            open_time=open_time,
            close_time=close_time,
            flatten_time=flatten_time,
            next_open=next_open,
            next_close=next_close,
        )

    except Exception:
        # API unavailable — fallback to weekend / US holidays / hardcoded
        if now_et.weekday() >= 5:
            return MarketSession(
                is_open=False,
                source="fallback_weekend",
            )
        if today in US_HOLIDAYS_2026:
            return MarketSession(
                is_open=False,
                source="fallback_holiday",
            )
        # Default: regular hours
        open_time = time(9, 30)
        close_time = time(16, 0)
        now_t = now_et.time()
        return MarketSession(
            is_open=open_time <= now_t < close_time,
            source="fallback_hardcoded",
            open_time=open_time,
            close_time=close_time,
            flatten_time=time(15, 55),
        )


# ──────────────────────────────────────────────────────────────────
#  Paper execution gateway
# ──────────────────────────────────────────────────────────────────


class PaperExecutionGateway:
    """Mockable paper-trading execution gateway.

    Simulates order submission and tracking without broker calls.
    Every order gets a synthetic ``order_id``.  Tracks pending orders
    and updates position state accordingly.

    Thread-safe: no — call from a single event loop.
    """

    def __init__(
        self,
        positions: Optional[PositionStore] = None,
        pending_orders: Optional[PendingOrderStore] = None,
        trade_ledger: Optional["TradeLedger"] = None,
    ) -> None:
        self._positions = positions or PositionStore()
        self._pending = pending_orders or PendingOrderStore()
        self._trade_ledger = trade_ledger
        self._entry_signals: dict[str, dict] = {}
        self._exit_order_meta: dict[str, dict] = {}

    @property
    def positions(self) -> PositionStore:
        return self._positions

    @property
    def pending(self) -> PendingOrderStore:
        return self._pending

    def _log_entry_fill(
        self,
        order: PendingOrder,
        pos: PositionStateModel,
        *,
        fill_price: Optional[float],
        filled_qty: int,
        event: str = "entry_fill",
    ) -> None:
        if not self._trade_ledger:
            return
        sig = self._entry_signals.get(order.symbol, {})
        self._trade_ledger.append({
            "event": event,
            "symbol": order.symbol,
            "side": order.side,
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "entry_order_id": order.order_id,
            "entry_fill_price": fill_price,
            "quantity": filled_qty,
            "current_shares": pos.current_shares,
            "entry_setup": sig.get("entry_setup"),
            "intended_risk": sig.get("intended_risk"),
        })

    def _log_exit_fill(
        self,
        order: PendingOrder,
        pos: PositionStateModel,
        *,
        exit_reason: Optional[str],
        exit_price: Optional[float],
        filled_qty: int,
        remaining: int,
    ) -> None:
        if not self._trade_ledger:
            return
        realized_pnl: Optional[float] = None
        win_loss: Optional[str] = None
        r_multiple: Optional[float] = None
        if pos.average_entry is not None and exit_price is not None:
            realized_pnl = round((exit_price - pos.average_entry) * filled_qty, 2)
            win_loss = "win" if realized_pnl > 0 else ("loss" if realized_pnl < 0 else "breakeven")
            if (
                pos.add_count == 0
                and pos.original_risk_per_share
                and pos.original_risk_per_share > 0
                and filled_qty > 0
            ):
                r_multiple = round(realized_pnl / (pos.original_risk_per_share * filled_qty), 2)
        self._trade_ledger.append({
            "event": "exit_fill",
            "symbol": order.symbol,
            "side": order.side,
            "exit_time": datetime.now(timezone.utc).isoformat(),
            "exit_order_id": order.order_id,
            "exit_reason": exit_reason,
            "exit_fill_price": exit_price,
            "quantity": filled_qty,
            "remaining_shares": remaining,
            "realized_pnl": realized_pnl,
            "win_loss": win_loss,
            "r_multiple": r_multiple,
        })

    # ── Entry ────────────────────────────────────────────────────

    def submit_entry(
        self,
        signal: EntrySignal,
    ) -> tuple[PendingOrder, PositionStateModel]:
        """Submit a paper entry order and create position state.

        Returns the pending order and the new ``PENDING_ENTRY`` position.
        Checks: symbol must not already be locked.
        """
        symbol = signal.symbol
        existing = self._positions.get(symbol)
        if existing is not None and existing.state not in (
            PositionState.NONE, PositionState.CLOSED,
        ):
            raise ValueError(f"Symbol {symbol} already has active position ({existing.state.value})")

        # Create position
        now = datetime.now(timezone.utc)
        position = PositionStateModel(
            symbol=symbol,
            state=PositionState.PENDING_ENTRY,
            entry_price=signal.entry_price,
            current_shares=signal.proposed_shares,
            average_entry=signal.entry_price,
            stop_price=signal.stop_price,
            original_risk_per_share=signal.risk_per_share,
            opened_at=now,
            updated_at=now,
            entry_setup=signal.entry_setup.value if hasattr(signal.entry_setup, "value") else str(signal.entry_setup),
        )
        self._positions.upsert(position)

        # Create pending order
        order = PendingOrder(
            symbol=symbol,
            order_id=f"paper_entry_{uuid.uuid4().hex[:8]}",
            order_type=OrderActionType.ENTRY,
            side="buy",
            qty=signal.proposed_shares,
            status="submitted",
            submitted_at=now,
            limit_price=signal.entry_price,
        )
        self._pending.add(order)

        # ponytail: track order_id on position for monitor-loop fill confirmation
        position.pending_order_id = order.order_id
        self._positions.upsert(position)

        # Store signal metadata for ledger logging
        self._entry_signals[symbol] = {
            "entry_setup": signal.entry_setup.value if hasattr(signal.entry_setup, "value") else signal.entry_setup,
            "intended_risk": signal.risk_amount,
        }

        return order, position

    def confirm_fill(self, order_id: str) -> PositionStateModel:
        """Simulate fill of a pending entry or add order.

        - ENTRY orders advance state to OPEN.
        - ADD orders update weighted average, advance to RUNNER,
          cancel old stops, and place a new combined stop.
        """
        for o in list(self._pending.all_pending()):
            if o.order_id == order_id:
                pos = self._positions.get(o.symbol)
                if pos is None:
                    raise ValueError(f"No position for {o.symbol}")

                if o.order_type == OrderActionType.ADD:
                    # ── ADD fill ──────────────────────────────────
                    if o.qty <= 0:
                        raise ValueError(f"Invalid ADD qty for {o.symbol}")
                    old_shares = pos.current_shares
                    old_avg = pos.average_entry or 0.0
                    add_qty = o.qty
                    add_price = o.limit_price or 0.0
                    total_shares = old_shares + add_qty
                    if total_shares > 0:
                        pos.average_entry = round(
                            (old_shares * old_avg + add_qty * add_price) / total_shares, 2
                        )
                    pos.current_shares = total_shares
                    pos.add_count += 1
                    transition_position(pos, PositionState.RUNNER)
                    self._positions.upsert(pos)
                    self._pending.resolve(order_id, "filled")

                    # Cancel old stops and place new combined stop
                    self.cancel_stale_orders(pos.symbol)
                    new_stop = max(o.stop_price or 0, pos.entry_price or 0)
                    self.place_stop(pos.symbol, new_stop, total_shares)
                    pos.stop_price = new_stop
                    pos.trailing_stop_price = new_stop
                    self._positions.upsert(pos)
                    self._log_entry_fill(o, pos, fill_price=add_price, filled_qty=add_qty, event="add_fill")
                    return pos

                # ── ENTRY fill ────────────────────────────────────
                transition_position(pos, PositionState.OPEN)
                self._positions.upsert(pos)
                self._pending.resolve(order_id, "filled")
                self._log_entry_fill(o, pos, fill_price=o.limit_price, filled_qty=o.qty)
                return pos
        raise ValueError(f"Order {order_id} not found in pending")

    # ── Add (scaling-in) ──────────────────────────────────────────

    def submit_add(
        self,
        symbol: str,
        qty: int,
        entry_price: float,
        stop_price: float,
    ) -> tuple[PendingOrder, PositionStateModel]:
        """Submit a scaling-in add order for a RUNNER position.

        Transitions RUNNER → ADDING and creates a pending ADD order.
        The existing stop is left in place until the add fills.
        """
        pos = self._positions.get(symbol)
        if pos is None or pos.state != PositionState.RUNNER:
            raise ValueError(f"Cannot add: {symbol} not in RUNNER state")

        now = datetime.now(timezone.utc)
        order = PendingOrder(
            symbol=symbol,
            order_id=f"paper_add_{uuid.uuid4().hex[:8]}",
            order_type=OrderActionType.ADD,
            side="buy",
            qty=qty,
            status="submitted",
            submitted_at=now,
            limit_price=entry_price,
            stop_price=stop_price,
        )
        self._pending.add(order)
        transition_position(pos, PositionState.ADDING)
        self._positions.upsert(pos)
        return order, pos

    # ── Stop / protection ────────────────────────────────────────

    def place_stop(
        self,
        symbol: str,
        stop_price: float,
        qty: int,
    ) -> PendingOrder:
        """Place a stop-loss order for an OPEN or RUNNER position."""
        pos = self._positions.get(symbol)
        if pos is None or pos.state not in (PositionState.OPEN, PositionState.RUNNER):
            raise ValueError(f"Cannot place stop: {symbol} not in OPEN or RUNNER state")

        order = PendingOrder(
            symbol=symbol,
            order_id=f"paper_stop_{uuid.uuid4().hex[:8]}",
            order_type=OrderActionType.STOP,
            side="sell",
            qty=qty,
            status="submitted",
            submitted_at=datetime.now(timezone.utc),
            stop_price=stop_price,
        )
        self._pending.add(order)
        pos.stop_price = stop_price
        self._positions.upsert(pos)
        return order

    def protect_position(
        self,
        symbol: str,
        stop_price: float,
        qty: Optional[int] = None,
    ) -> Optional[PendingOrder]:
        """Ensure a position has active stop protection. Idempotent.

        Returns None only if the position already has a verified pending
        stop at the requested price.  Local stop_price alone is not proof.
        """
        pos = self._positions.get(symbol)
        if pos is None:
            return None
        q = qty if qty is not None else pos.current_shares
        if pos.stop_price == stop_price and self._has_pending_stop(symbol):
            return None  # already protected with a verified pending stop
        return self.place_stop(symbol, stop_price, q)

    def _has_pending_stop(self, symbol: str) -> bool:
        for o in self._pending.get_for_symbol(symbol):
            if o.order_type == OrderActionType.STOP and o.status == "submitted":
                return True
        return False

    # ── Cancel ───────────────────────────────────────────────────

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order. Returns True if found and cancelled."""
        for o in self._pending.all_pending():
            if o.order_id == order_id:
                self._pending.resolve(order_id, "cancelled")
                return True
        return False

    def cancel_stale_orders(self, symbol: str) -> int:
        """Cancel all pending orders for a symbol. Returns count cancelled."""
        count = 0
        for o in list(self._pending.get_for_symbol(symbol)):
            if self.cancel_order(o.order_id):
                count += 1
        return count

    # ── Exit ─────────────────────────────────────────────────────

    def submit_exit(
        self,
        symbol: str,
        reason: str,
        *,
        exit_pct: int = 100,
        exit_price: Optional[float] = None,
        pnl: Optional[float] = None,
    ) -> tuple[PendingOrder, PositionStateModel]:
        """Submit a full or partial exit for a position.

        Cancels existing protective orders before creating sell order
        to prevent stale-stop / unintended-short hazard.
        """
        pos = self._positions.get(symbol)
        if pos is None:
            raise ValueError(f"No position for {symbol}")

        self.cancel_stale_orders(symbol)

        transition_position(pos, PositionState.EXITING, force=True)

        order = PendingOrder(
            symbol=symbol,
            order_id=f"paper_exit_{uuid.uuid4().hex[:8]}",
            order_type=OrderActionType.EXIT,
            side="sell",
            qty=int(pos.current_shares * exit_pct / 100),
            status="submitted",
            submitted_at=datetime.now(timezone.utc),
        )
        self._pending.add(order)
        self._positions.upsert(pos)

        # Store exit metadata for ledger logging
        self._exit_order_meta[order.order_id] = {
            "reason": reason,
            "exit_price": exit_price,
        }

        return order, pos

    def confirm_exit_fill(self, order_id: str) -> PositionStateModel:
        """Mark an exit as filled, reduce shares. Close only if zero remain.

        When shares remain after a partial exit, transition back to OPEN
        and place a new stop for the remaining quantity.
        """
        for o in self._pending.all_pending():
            if o.order_id == order_id:
                pos = self._positions.get(o.symbol)
                exit_qty = o.qty
                meta = self._exit_order_meta.get(order_id, {})
                exit_reason = meta.get("reason")
                exit_price = meta.get("exit_price")
                if pos is not None:
                    if exit_price is not None and pos.average_entry and exit_qty > 0:
                        # ponytail: += not = — accumulate across partial exits
                        pos.realized_pnl = (pos.realized_pnl or 0.0) + (exit_price - pos.average_entry) * exit_qty
                    remaining = max(pos.current_shares - exit_qty, 0)
                    pos.current_shares = remaining
                    pos.updated_at = datetime.now(timezone.utc)
                    if remaining == 0:
                        pos.state = PositionState.CLOSED
                    else:
                        transition_position(pos, PositionState.OPEN, force=True)
                        if pos.stop_price is not None:
                            self.place_stop(pos.symbol, pos.stop_price, remaining)
                    self._positions.upsert(pos)
                else:
                    remaining = 0
                self._pending.resolve(order_id, "filled")
                if pos is not None:
                    self._log_exit_fill(
                        o,
                        pos,
                        exit_reason=exit_reason,
                        exit_price=exit_price,
                        filled_qty=exit_qty,
                        remaining=remaining,
                    )
                    if remaining == 0:
                        self._entry_signals.pop(o.symbol, None)
                    self._exit_order_meta.pop(order_id, None)
                if pos is None:
                    raise ValueError(f"No position for {o.symbol}")
                return pos
        raise ValueError(f"Order {order_id} not found")

    # ── State queries ────────────────────────────────────────────

    def is_symbol_locked(self, symbol: str) -> bool:
        """Check if a symbol is locked from new entries."""
        pos = self._positions.get(symbol)
        if pos is not None and pos.state not in (PositionState.NONE, PositionState.CLOSED):
            return True
        return self._pending.has_pending_buy(symbol)

    def mark_unprotected(self, symbol: str) -> PositionStateModel:
        """Transition an OPEN, RUNNER, or EXITING position to UNPROTECTED state.

        EXITING positions that timed out can be escalated to UNPROTECTED
        so the exit engine can handle them.  RUNNER positions that lose
        their trailing stop or experience persistent data outage are also
        escalated to UNPROTECTED.
        """
        pos = self._positions.get(symbol)
        if pos is None:
            raise ValueError(f"No position for {symbol}")
        if pos.state not in (PositionState.OPEN, PositionState.RUNNER, PositionState.EXITING, PositionState.PENDING_ENTRY):
            raise ValueError(
                f"Cannot mark unprotected: {symbol} is {pos.state.value}, not OPEN/RUNNER/EXITING"
            )
        transition_position(pos, PositionState.UNPROTECTED, force=True)
        self._positions.upsert(pos)
        return pos

    def get_unprotected_positions(self) -> list[str]:
        """Return symbols of positions with no active stop protection.

        Includes both OPEN positions without a pending stop and
        positions explicitly marked UNPROTECTED.
        """
        unprotected: list[str] = []
        for pos in self._positions.all_open():
            if pos.state == PositionState.UNPROTECTED:
                unprotected.append(pos.symbol)
            elif pos.state == PositionState.OPEN and not self._has_pending_stop(pos.symbol):
                unprotected.append(pos.symbol)
        return unprotected


# ──────────────────────────────────────────────────────────────────
#  Alpaca Paper execution gateway (real orders, paper account)
# ──────────────────────────────────────────────────────────────────


class AlpacaExecutionGateway(PaperExecutionGateway):
    """Execution gateway that submits real orders to Alpaca paper API.

    Extends ``PaperExecutionGateway`` — overrides submit/confirm/protect
    to use ``TradingClient``.  T6.5: API failures produce explicit errors,
    never silent synthetic fills.  T6.7: order statuses handled explicitly
    (filled, partially_filled, rejected, canceled, expired, pending).
    """

    def __init__(
        self,
        *,
        positions: Optional[PositionStore] = None,
        pending_orders: Optional[PendingOrderStore] = None,
        trade_ledger: Optional["TradeLedger"] = None,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        paper: bool = True,
    ) -> None:
        super().__init__(positions=positions, pending_orders=pending_orders, trade_ledger=trade_ledger)
        self._api_key = api_key or os.getenv("ALPACA_API_KEY")
        self._secret_key = secret_key or os.getenv("ALPACA_SECRET_KEY")
        self._paper = paper
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from alpaca.trading import TradingClient
            self._client = TradingClient(self._api_key, self._secret_key, paper=self._paper)
        return self._client

    # ── Status helpers (T6.7) ───────────────────────────────────

    _FILL_TERMINAL = frozenset({"filled"})
    _PARTIAL_FILL = frozenset({"partially_filled"})
    _ERROR_TERMINAL = frozenset({"rejected", "canceled", "expired", "done_for_day"})
    _PENDING_OPEN = frozenset({"new", "accepted", "pending_new", "pending_cancel", "pending_replace"})

    @staticmethod
    def _map_alpaca_status(raw_status: str) -> str:
        """Normalise Alpaca order status to a canonical key."""
        s = raw_status.lower().replace("_", "")
        if s in ("fill", "filled"):
            return "filled"
        if s in ("partialfill", "partiallyfilled"):
            return "partially_filled"
        if s in ("rejected", "canceled", "expired", "doneforday"):
            return s if s != "doneforday" else "expired"
        return "pending"

    # ── Entry ───────────────────────────────────────────────────

    def submit_entry(self, signal: EntrySignal) -> tuple[PendingOrder, PositionStateModel]:
        """Submit LIMIT buy order to Alpaca paper account (T6.5: no synthetic fallback).

        On API failure, raises RuntimeError — caller must handle escalation.
        """
        symbol = signal.symbol
        existing = self._positions.get(symbol)
        if existing is not None and existing.state not in (
            PositionState.NONE, PositionState.CLOSED,
        ):
            raise ValueError(f"Symbol {symbol} already has active position ({existing.state.value})")

        now = datetime.now(timezone.utc)

        # Create local state first
        position = PositionStateModel(
            symbol=symbol,
            state=PositionState.PENDING_ENTRY,
            entry_price=signal.entry_price,
            current_shares=signal.proposed_shares,
            average_entry=signal.entry_price,
            stop_price=signal.stop_price,
            original_risk_per_share=signal.risk_per_share,
            opened_at=now, updated_at=now,
            entry_setup=signal.entry_setup.value if hasattr(signal.entry_setup, "value") else str(signal.entry_setup),
        )
        self._positions.upsert(position)

        # Submit to Alpaca — T6.5: no synthetic fallback on failure
        try:
            from alpaca.trading.requests import LimitOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce
            req = LimitOrderRequest(
                symbol=symbol,
                qty=signal.proposed_shares,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
                limit_price=signal.entry_price,
            )
            alpaca_order = self.client.submit_order(req)
            order_id = str(alpaca_order.id)
        except Exception as e:
            # T6.5: Clean up local state on API failure — no fake order
            self._positions.remove(symbol)
            raise RuntimeError(
                f"Alpaca submit_entry failed for {symbol}: {e}"
            ) from e

        order = PendingOrder(
            symbol=symbol,
            order_id=order_id,
            order_type=OrderActionType.ENTRY,
            side="buy",
            qty=signal.proposed_shares,
            status="submitted",
            submitted_at=now,
            limit_price=signal.entry_price,
        )
        self._pending.add(order)

        # ponytail: track order_id on position for monitor-loop fill confirmation
        position.pending_order_id = order.order_id
        self._positions.upsert(position)
        self._entry_signals[symbol] = {
            "entry_setup": signal.entry_setup.value if hasattr(signal.entry_setup, "value") else signal.entry_setup,
            "intended_risk": signal.risk_amount,
        }
        return order, position

    def confirm_fill(self, order_id: str) -> PositionStateModel:
        """Check Alpaca order status (T6.7). No synthetic fill.

        - ENTRY: filled → OPEN, partial → OPEN with filled qty, error → ERROR, pending → retry
        - ADD: filled → RUNNER with weighted avg, partial → RUNNER, error → RUNNER (no mutation), pending → retry
        """
        for o in list(self._pending.all_pending()):
            if o.order_id == order_id:
                pos = self._positions.get(o.symbol)
                if pos is None:
                    raise ValueError(f"No position for {o.symbol}")

                # Check real Alpaca order status (T6.7)
                try:
                    alpaca_order = self.client.get_order_by_id(order_id)
                    raw_status = str(alpaca_order.status)
                    status = self._map_alpaca_status(raw_status)
                except Exception as e:
                    raise RuntimeError(
                        f"Alpaca confirm_fill check failed for {o.symbol} order {order_id}: {e}"
                    ) from e

                # ── ADD order handling ────────────────────────────────
                if o.order_type == OrderActionType.ADD:
                    if status == "filled":
                        filled_price = float(getattr(alpaca_order, 'filled_avg_price', None) or o.limit_price or 0)
                        filled_qty = int(float(getattr(alpaca_order, 'filled_qty', None) or o.qty))
                        old_shares = pos.current_shares
                        old_avg = pos.average_entry or 0.0
                        total_shares = old_shares + filled_qty
                        if total_shares > 0:
                            pos.average_entry = round(
                                (old_shares * old_avg + filled_qty * filled_price) / total_shares, 2
                            )
                        pos.current_shares = total_shares
                        pos.add_count += 1
                        transition_position(pos, PositionState.RUNNER)
                        pos.updated_at = datetime.now(timezone.utc)
                        self._positions.upsert(pos)
                        self._pending.resolve(order_id, "filled")
                        self.cancel_stale_orders(pos.symbol)
                        new_stop = max(o.stop_price or 0, pos.entry_price or 0)
                        self.place_stop(pos.symbol, new_stop, total_shares)
                        pos.stop_price = new_stop
                        pos.trailing_stop_price = new_stop
                        self._positions.upsert(pos)
                        self._log_entry_fill(o, pos, fill_price=filled_price, filled_qty=filled_qty, event="add_fill")
                        return pos

                    elif status == "partially_filled":
                        filled_qty = int(float(getattr(alpaca_order, 'filled_qty', None) or 0))
                        if filled_qty > 0:
                            filled_price = float(getattr(alpaca_order, 'filled_avg_price', None) or o.limit_price or 0)
                            old_shares = pos.current_shares
                            old_avg = pos.average_entry or 0.0
                            total_shares = old_shares + filled_qty
                            if total_shares > 0:
                                pos.average_entry = round(
                                    (old_shares * old_avg + filled_qty * filled_price) / total_shares, 2
                                )
                            pos.current_shares = total_shares
                            pos.add_count += 1
                            # Cancel unfilled remainder at broker
                            try:
                                self.client.cancel_order_by_id(order_id)
                            except Exception:
                                logger.warning(
                                    "Cancel of unfilled add order %s failed — continuing",
                                    order_id,
                                )
                            transition_position(pos, PositionState.RUNNER)
                            pos.updated_at = datetime.now(timezone.utc)
                            self._positions.upsert(pos)
                            self._pending.resolve(order_id, "partially_filled")
                            self.cancel_stale_orders(pos.symbol)
                            new_stop = max(o.stop_price or 0, pos.entry_price or 0)
                            self.place_stop(pos.symbol, new_stop, total_shares)
                            pos.stop_price = new_stop
                            pos.trailing_stop_price = new_stop
                            self._positions.upsert(pos)
                            self._log_entry_fill(o, pos, fill_price=filled_price, filled_qty=filled_qty, event="add_fill")
                            logger.info(
                                "Add partial fill for %s: %d/%d shares filled, remainder cancelled",
                                o.symbol, filled_qty, o.qty,
                            )
                        return pos

                    elif status in self._ERROR_TERMINAL:
                        # Rejection: return to RUNNER without mutation
                        pos.state = PositionState.RUNNER
                        pos.updated_at = datetime.now(timezone.utc)
                        self._positions.upsert(pos)
                        self._pending.resolve(order_id, status)
                        raise RuntimeError(
                            f"Add order {order_id} for {o.symbol} was {status}"
                        )

                    else:
                        # pending — no action, caller retries next cycle
                        logger.debug(
                            "Add order %s for %s is %s — awaiting fill",
                            order_id, o.symbol, raw_status,
                        )
                        return pos

                # ── ENTRY order handling ──────────────────────────────
                if status == "filled":
                    filled_price = float(getattr(alpaca_order, 'filled_avg_price', None) or pos.entry_price)
                    filled_qty = int(float(getattr(alpaca_order, 'filled_qty', None) or o.qty))
                    pos.entry_price = filled_price
                    pos.average_entry = filled_price
                    pos.current_shares = filled_qty
                    transition_position(pos, PositionState.OPEN)
                    pos.updated_at = datetime.now(timezone.utc)
                    self._positions.upsert(pos)
                    self._pending.resolve(order_id, "filled")
                    self._log_entry_fill(o, pos, fill_price=filled_price, filled_qty=filled_qty)
                    return pos

                elif status == "partially_filled":
                    filled_qty = int(float(getattr(alpaca_order, 'filled_qty', None) or 0))
                    if filled_qty > 0:
                        filled_price = float(getattr(alpaca_order, 'filled_avg_price', None) or pos.entry_price)
                        pos.entry_price = filled_price
                        pos.average_entry = filled_price
                        pos.current_shares = filled_qty
                        transition_position(pos, PositionState.OPEN)
                        pos.updated_at = datetime.now(timezone.utc)
                        self._positions.upsert(pos)
                        self._pending.resolve(order_id, "partially_filled")
                        self._log_entry_fill(o, pos, fill_price=filled_price, filled_qty=filled_qty)
                        logger.info(
                            "Partial fill for %s: %d/%d shares filled",
                            o.symbol, filled_qty, o.qty,
                        )
                    return pos
                    # Note: if filled_qty is 0, position stays PENDING_ENTRY

                elif status in self._ERROR_TERMINAL:
                    pos.state = PositionState.ERROR
                    pos.updated_at = datetime.now(timezone.utc)
                    self._positions.upsert(pos)
                    self._pending.resolve(order_id, status)
                    self._entry_signals.pop(o.symbol, None)
                    raise RuntimeError(
                        f"Entry order {order_id} for {o.symbol} was {status} — position set to ERROR"
                    )

                else:
                    # pending — no action, caller retries next cycle
                    logger.debug(
                        "Entry order %s for %s is %s — awaiting fill",
                        order_id, o.symbol, raw_status,
                    )
                    return pos

        raise ValueError(f"Order {order_id} not found in pending")

    # ── Add (scaling-in) ──────────────────────────────────────────

    def submit_add(
        self,
        symbol: str,
        qty: int,
        entry_price: float,
        stop_price: float,
    ) -> tuple[PendingOrder, PositionStateModel]:
        """Submit LIMIT buy add order to Alpaca paper (T6.5: no synthetic fallback)."""
        pos = self._positions.get(symbol)
        if pos is None or pos.state != PositionState.RUNNER:
            raise ValueError(f"Cannot add: {symbol} not in RUNNER state")

        now = datetime.now(timezone.utc)
        try:
            from alpaca.trading.requests import LimitOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce
            req = LimitOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
                limit_price=entry_price,
            )
            alpaca_order = self.client.submit_order(req)
            order_id = str(alpaca_order.id)
        except Exception as e:
            raise RuntimeError(
                f"Alpaca submit_add failed for {symbol}: {e}"
            ) from e

        order = PendingOrder(
            symbol=symbol,
            order_id=order_id,
            order_type=OrderActionType.ADD,
            side="buy",
            qty=qty,
            status="submitted",
            submitted_at=now,
            limit_price=entry_price,
            stop_price=stop_price,
        )
        self._pending.add(order)
        transition_position(pos, PositionState.ADDING)
        self._positions.upsert(pos)
        return order, pos

    # ── Stop / protection ───────────────────────────────────────

    def place_stop(self, symbol: str, stop_price: float, qty: int) -> PendingOrder:
        """Place STOP sell order at Alpaca. T6.5: no synthetic fallback."""
        pos = self._positions.get(symbol)
        if pos is None or pos.state not in (PositionState.OPEN, PositionState.RUNNER):
            raise ValueError(f"Cannot place stop: {symbol} not in OPEN or RUNNER state")

        now = datetime.now(timezone.utc)
        try:
            from alpaca.trading.requests import StopOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce
            req = StopOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.GTC,
                stop_price=stop_price,
            )
            alpaca_order = self.client.submit_order(req)
            order_id = str(alpaca_order.id)
        except Exception as e:
            raise RuntimeError(
                f"Alpaca stop placement failed for {symbol}: {e}"
            ) from e

        order = PendingOrder(
            symbol=symbol,
            order_id=order_id,
            order_type=OrderActionType.STOP,
            side="sell",
            qty=qty,
            status="submitted",
            submitted_at=now,
            stop_price=stop_price,
        )
        self._pending.add(order)
        pos.stop_price = stop_price
        self._positions.upsert(pos)
        return order

    # ── Cancel ──────────────────────────────────────────────────

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order at Alpaca, then resolve locally.

        Handles broker errors gracefully (e.g. already-terminal or not-found
        orders) so the caller can proceed with exit without crashing.
        """
        for order in list(self._pending.all_pending()):
            if order.order_id == order_id:
                try:
                    self.client.cancel_order_by_id(order_id)
                except Exception:
                    logger.warning(
                        "Broker cancel failed for order %s, continuing with local cleanup",
                        order_id,
                    )
                self._pending.resolve(order_id, "cancelled")
                return True
        try:
            self.client.cancel_order_by_id(order_id)
            return True
        except Exception:
            logger.warning(
                "Broker cancel failed for untracked order %s", order_id,
            )
        return False

    # ── Exit ────────────────────────────────────────────────────

    def submit_exit(self, symbol: str, reason: str, *, exit_pct: int = 100,
                    exit_price: Optional[float] = None, pnl: Optional[float] = None,
                    ) -> tuple[PendingOrder, PositionStateModel]:
        """Submit MARKET sell to Alpaca. T6.5: no synthetic fallback.

        Transitions to EXITING only after the broker call succeeds.
        On API failure, restores the prior stop protection (if any)
        so the position does not become an unprotected zombie.
        """
        pos = self._positions.get(symbol)
        if pos is None:
            raise ValueError(f"No position for {symbol}")

        # Capture stop state before cancelling (for rollback on failure)
        had_stop = self._has_pending_stop(symbol)
        existing_stop = pos.stop_price
        existing_qty = pos.current_shares

        self.cancel_stale_orders(symbol)

        qty = int(pos.current_shares * exit_pct / 100)
        now = datetime.now(timezone.utc)

        try:
            from alpaca.trading.requests import MarketOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce
            req = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
            )
            alpaca_order = self.client.submit_order(req)
            order_id = str(alpaca_order.id)
        except Exception as e:
            # Restore stop protection on submit failure if we had one
            if had_stop and existing_stop is not None and existing_qty > 0:
                try:
                    self.place_stop(symbol, existing_stop, existing_qty)
                except Exception as restore_err:
                    logger.error(
                        "Failed to restore stop for %s after exit failure: %s",
                        symbol, restore_err,
                    )
            raise RuntimeError(
                f"Alpaca exit failed for {symbol}: {e}"
            ) from e

        # Only transition to EXITING after successful broker submission
        transition_position(pos, PositionState.EXITING, force=True)

        order = PendingOrder(
            symbol=symbol,
            order_id=order_id,
            order_type=OrderActionType.EXIT,
            side="sell",
            qty=qty,
            status="submitted",
            submitted_at=now,
        )
        self._pending.add(order)
        self._positions.upsert(pos)
        self._exit_order_meta[order.order_id] = {
            "reason": reason,
            "exit_price": exit_price,
        }
        return order, pos

    def confirm_exit_fill(self, order_id: str) -> PositionStateModel:
        """Confirm exit fill from Alpaca (T6.7). No synthetic fill.

        - filled → reduce shares, close if zero
        - partially_filled → reduce only filled qty
        - rejected/canceled/expired → mark ERROR
        - pending → stay EXITING (caller must retry)
        """
        for o in list(self._pending.all_pending()):
            if o.order_id == order_id:
                pos = self._positions.get(o.symbol)
                if pos is None:
                    raise ValueError(f"No position for {o.symbol}")

                # Check real Alpaca order status (T6.7)
                try:
                    alpaca_order = self.client.get_order_by_id(order_id)
                    raw_status = str(alpaca_order.status)
                    status = self._map_alpaca_status(raw_status)
                except Exception as e:
                    raise RuntimeError(
                        f"Alpaca confirm_exit_fill check failed for {o.symbol} order {order_id}: {e}"
                    ) from e

                if status == "filled":
                    filled_qty = int(float(getattr(alpaca_order, 'filled_qty', None) or o.qty))
                    avg_price = float(getattr(alpaca_order, 'filled_avg_price', None) or 0)
                    if avg_price > 0 and pos.average_entry:
                        # ponytail: += not = — accumulate across partial exits
                        pos.realized_pnl = (pos.realized_pnl or 0.0) + (avg_price - pos.average_entry) * filled_qty
                    remaining = max(pos.current_shares - filled_qty, 0)
                    pos.current_shares = remaining
                    pos.updated_at = datetime.now(timezone.utc)
                    if remaining == 0:
                        pos.state = PositionState.CLOSED
                    else:
                        transition_position(pos, PositionState.OPEN, force=True)
                        if pos.stop_price is not None:
                            self.place_stop(pos.symbol, pos.stop_price, remaining)
                    self._positions.upsert(pos)
                    self._pending.resolve(order_id, "filled")
                    meta = self._exit_order_meta.get(order_id, {})
                    self._log_exit_fill(
                        o,
                        pos,
                        exit_reason=meta.get("reason"),
                        exit_price=avg_price if avg_price > 0 else meta.get("exit_price"),
                        filled_qty=filled_qty,
                        remaining=remaining,
                    )
                    if remaining == 0:
                        self._entry_signals.pop(o.symbol, None)
                    self._exit_order_meta.pop(order_id, None)
                    return pos

                elif status == "partially_filled":
                    filled_qty = int(float(getattr(alpaca_order, 'filled_qty', None) or 0))
                    if filled_qty > 0:
                        avg_price = float(getattr(alpaca_order, 'filled_avg_price', None) or 0)
                        if avg_price > 0 and pos.average_entry:
                            # ponytail: += not = — accumulate across partial exits
                            pos.realized_pnl = (pos.realized_pnl or 0.0) + (avg_price - pos.average_entry) * filled_qty
                        remaining = max(pos.current_shares - filled_qty, 0)
                        try:
                            self.client.cancel_order_by_id(order_id)
                        except Exception:
                            logger.warning(
                                "Cancel of unfilled exit order %s failed — continuing",
                                order_id,
                            )
                        pos.current_shares = remaining
                        pos.updated_at = datetime.now(timezone.utc)
                        if remaining == 0:
                            pos.state = PositionState.CLOSED
                        else:
                            transition_position(pos, PositionState.OPEN, force=True)
                            if pos.stop_price is not None:
                                self.place_stop(pos.symbol, pos.stop_price, remaining)
                        self._positions.upsert(pos)
                        self._pending.resolve(order_id, "partially_filled")
                        meta = self._exit_order_meta.get(order_id, {})
                        self._log_exit_fill(
                            o,
                            pos,
                            exit_reason=meta.get("reason"),
                            exit_price=avg_price if avg_price > 0 else meta.get("exit_price"),
                            filled_qty=filled_qty,
                            remaining=remaining,
                        )
                        if remaining == 0:
                            self._entry_signals.pop(o.symbol, None)
                        self._exit_order_meta.pop(order_id, None)
                        logger.info(
                            "Exit partial fill for %s: %d/%d shares filled",
                            o.symbol, filled_qty, o.qty,
                        )
                    return pos

                elif status in self._ERROR_TERMINAL:
                    pos.state = PositionState.ERROR
                    pos.updated_at = datetime.now(timezone.utc)
                    self._positions.upsert(pos)
                    self._pending.resolve(order_id, status)
                    self._entry_signals.pop(o.symbol, None)
                    self._exit_order_meta.pop(order_id, None)
                    raise RuntimeError(
                        f"Exit order {order_id} for {o.symbol} was {status} — position set to ERROR"
                    )

                else:
                    # pending — no action, caller retries next cycle
                    logger.debug(
                        "Exit order %s for %s is %s — awaiting fill",
                        order_id, o.symbol, raw_status,
                    )
                    return pos

        raise ValueError(f"Order {order_id} not found")


# ──────────────────────────────────────────────────────────────────
#  Reconciliation (SPEC §15.4)
# ──────────────────────────────────────────────────────────────────


def reconcile_positions(
    *,
    broker_positions: dict[str, tuple[int, float]],  # symbol → (qty, avg_entry)
    local_store: PositionStore,
    pending_store: PendingOrderStore,
) -> list[dict]:
    """Compare broker truth against local state and return required actions.

    Each action is a dict with keys: ``action``, ``symbol``, ``reason``, and
    optional ``qty``, ``avg_entry``, ``stop_price``.

    8 reconciliation cases from SPEC §15.4:

    1. broker has position, local has none → insert local OPEN, mark needs protection
    2. broker qty matches local → verify stop exists, replace if missing
    3. broker qty < local qty → assume partial fill, update local, re-protect
    4. broker qty > local qty → update local, re-protect, log warning
    5. broker has no position, local has open → close local (broker truth wins)
    6. broker has no position, local has pending → cancel stale orders
    7. irreconcilable mismatch → mark ERROR, alert
    8. broker unreachable → mark UNPROTECTED (handled by caller)
    """
    actions: list[dict] = []

    local_by_symbol: dict[str, PositionStateModel] = {
        p.symbol: p for p in local_store.all_positions()
    }
    all_symbols = set(broker_positions.keys()) | set(local_by_symbol.keys())

    for symbol in all_symbols:
        broker = broker_positions.get(symbol)  # (qty, avg_entry) or None
        local = local_by_symbol.get(symbol)

        # Case 1: broker has position, local has none
        if broker is not None and local is None:
            qty, avg_entry = broker
            pos = PositionStateModel(
                symbol=symbol,
                state=PositionState.OPEN,
                current_shares=qty,
                average_entry=avg_entry,
                entry_price=avg_entry,
                opened_at=datetime.now(timezone.utc),
            )
            local_store.upsert(pos)
            actions.append({
                "action": "insert_protect",
                "symbol": symbol,
                "reason": "broker_has_position_local_none",
                "qty": qty,
                "avg_entry": avg_entry,
            })
            continue

        # Case 5: broker has no position, local is open/pending
        if broker is None and local is not None:
            if local.state not in (PositionState.NONE, PositionState.CLOSED):
                local.state = PositionState.CLOSED
                local.current_shares = 0
                local.updated_at = datetime.now(timezone.utc)
                local_store.upsert(local)
                actions.append({
                    "action": "close_local",
                    "symbol": symbol,
                    "reason": "broker_no_position_local_active",
                })
            # Case 6: cancel stale pending orders
            for o in pending_store.get_for_symbol(symbol):
                pending_store.resolve(o.order_id, "reconciled_stale")
                actions.append({
                    "action": "cancel_stale_order",
                    "symbol": symbol,
                    "reason": "stale_order_no_broker_position",
                    "order_id": o.order_id,
                })
            continue

        # Both have position
        if broker is not None and local is not None:
            b_qty, b_avg = broker

            # Case 7: irreconcilable — broker has 0 qty with local active
            if b_qty <= 0 and local.current_shares > 0:
                local.state = PositionState.ERROR
                local_store.upsert(local)
                actions.append({
                    "action": "irreconcilable",
                    "symbol": symbol,
                    "reason": "broker_qty_zero_local_active",
                })
                continue

            # Case 2: qty matches
            if b_qty == local.current_shares:
                actions.append({
                    "action": "verify_stop",
                    "symbol": symbol,
                    "reason": "qty_matches_verify_protection",
                })

            # Case 3: broker qty < local qty (partial fill happened)
            elif b_qty < local.current_shares:
                old_local_shares = local.current_shares
                local.current_shares = b_qty
                local.average_entry = b_avg if b_avg > 0 else local.average_entry
                local_store.upsert(local)
                actions.append({
                    "action": "update_qty_reprotect",
                    "symbol": symbol,
                    "reason": f"broker_qty_less:{b_qty}<{old_local_shares}",
                    "qty": b_qty,
                })

            # Case 4: broker qty > local qty (missed fill)
            else:
                old_local_shares = local.current_shares
                local.current_shares = b_qty
                local.average_entry = b_avg if b_avg > 0 else local.average_entry
                local_store.upsert(local)
                # Task 5: detect missed ADD fill for RUNNER / ADDING positions
                if local.state in (PositionState.RUNNER, PositionState.ADDING):
                    actions.append({
                        "action": "update_qty_reprotect_add_missed",
                        "symbol": symbol,
                        "reason": f"broker_qty_more_add_missed:{b_qty}>{old_local_shares}",
                        "qty": b_qty,
                        "avg_entry": b_avg,
                    })
                else:
                    actions.append({
                        "action": "update_qty_reprotect_warning",
                        "symbol": symbol,
                        "reason": f"broker_qty_more:{b_qty}>{old_local_shares}",
                        "qty": b_qty,
                    })

    return actions


def reconcile_open_orders(
    *,
    broker_orders: list[PendingOrder],
    local_store: PositionStore,
    pending_store: PendingOrderStore,
) -> list[dict]:
    """Reconcile broker open orders against local pending-order state.

    Broker open orders are execution truth.  Local pending orders missing at
    the broker are removed; missing broker STOP protection escalates the
    local position to UNPROTECTED.  Broker orders not represented locally are
    imported when they belong to an active local position, or returned as
    cancel actions when orphaned.
    """
    actions: list[dict] = []
    broker_by_id = {o.order_id: o for o in broker_orders}
    local_by_id = {o.order_id: o for o in pending_store.all_pending()}

    for order_id, local_order in list(local_by_id.items()):
        if order_id in broker_by_id:
            continue
        pending_store.resolve(order_id, "broker_missing")
        actions.append({
            "action": "drop_local_missing_broker_order",
            "symbol": local_order.symbol,
            "reason": "local_pending_order_missing_at_broker",
            "order_id": order_id,
        })
        pos = local_store.get(local_order.symbol)
        if (
            local_order.order_type == OrderActionType.STOP
            and pos is not None
            and pos.state in (PositionState.OPEN, PositionState.RUNNER)
        ):
            transition_position(pos, PositionState.UNPROTECTED, force=True)
            local_store.upsert(pos)
            actions.append({
                "action": "missing_broker_stop",
                "symbol": local_order.symbol,
                "reason": "local_stop_missing_at_broker",
                "order_id": order_id,
            })

    for order_id, broker_order in broker_by_id.items():
        if order_id in local_by_id:
            continue
        pos = local_store.get(broker_order.symbol)
        if pos is None or pos.state in (PositionState.NONE, PositionState.CLOSED):
            actions.append({
                "action": "cancel_orphan_broker_order",
                "symbol": broker_order.symbol,
                "reason": "broker_order_without_local_position",
                "order_id": order_id,
            })
            continue
        pending_store.add(broker_order)
        actions.append({
            "action": "import_broker_order",
            "symbol": broker_order.symbol,
            "reason": "broker_order_missing_locally",
            "order_id": order_id,
        })

    return actions


# ──────────────────────────────────────────────────────────────────
#  Broker snapshot for Alpaca mode (T6.6)
# ──────────────────────────────────────────────────────────────────


def build_alpaca_broker_snapshot(gateway: AlpacaExecutionGateway) -> Optional[dict[str, tuple[int, float]]]:
    """Fetch current positions from Alpaca for broker snapshot (T6.6).

    Returns a dict ``{symbol: (qty, avg_entry_price)}`` on success,
    or ``None`` if the broker is unreachable (T6.2 policy).
    """
    try:
        raw_positions = gateway.client.get_all_positions()
        positions = raw_positions.values() if isinstance(raw_positions, dict) else (raw_positions or [])
        result: dict[str, tuple[int, float]] = {}
        for pos in positions:
            qty = int(float(pos.qty))
            avg_entry = float(getattr(pos, 'avg_entry_price', None) or pos.cost_basis or 0)
            result[pos.symbol] = (qty, avg_entry)
        return result
    except Exception:
        logger.exception("Failed to fetch broker snapshot from Alpaca")
        return None


def build_alpaca_account_equity(gateway: AlpacaExecutionGateway) -> Optional[float]:
    """Fetch current Alpaca account equity for runtime sizing/risk caps."""
    try:
        account = gateway.client.get_account()
        raw_equity = getattr(account, "equity", None) or getattr(account, "portfolio_value", None)
        return float(raw_equity) if raw_equity not in (None, "") else None
    except Exception:
        logger.exception("Failed to fetch Alpaca account equity")
        return None


def _pending_order_from_alpaca_order(order) -> PendingOrder:
    """Convert an Alpaca order model into local PendingOrder shape."""
    order_type_raw = str(
        getattr(order, "order_type", None) or getattr(order, "type", "")
    ).lower()
    side = str(getattr(order, "side", "")).lower()
    if "stop" in order_type_raw:
        order_type = OrderActionType.STOP
    elif side == "buy":
        order_type = OrderActionType.ENTRY
    else:
        order_type = OrderActionType.EXIT

    stop_raw = getattr(order, "stop_price", None)
    stop_price = float(stop_raw) if stop_raw not in (None, "") else None
    return PendingOrder(
        symbol=str(getattr(order, "symbol")),
        order_id=str(getattr(order, "id")),
        order_type=order_type,
        side=side,
        qty=int(float(getattr(order, "qty", 0) or 0)),
        status=str(getattr(order, "status", "submitted")),
        submitted_at=datetime.now(timezone.utc),
        stop_price=stop_price,
    )


def build_alpaca_open_order_snapshot(gateway: AlpacaExecutionGateway) -> Optional[list[PendingOrder]]:
    """Fetch Alpaca open orders for startup order reconciliation."""
    try:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        req = GetOrdersRequest(status=QueryOrderStatus.OPEN)
        orders = gateway.client.get_orders(filter=req)
        return [_pending_order_from_alpaca_order(order) for order in orders]
    except Exception as exc:
        logger.warning("Alpaca open-order snapshot failed: {}", exc)
        return None
