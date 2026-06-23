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
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

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
    ) -> None:
        self._positions = positions or PositionStore()
        self._pending = pending_orders or PendingOrderStore()

    @property
    def positions(self) -> PositionStore:
        return self._positions

    @property
    def pending(self) -> PendingOrderStore:
        return self._pending

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
            opened_at=now,
            updated_at=now,
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

        return order, position

    def confirm_fill(self, order_id: str) -> PositionStateModel:
        """Simulate fill of a pending entry order. Advances state to OPEN."""
        for o in self._pending.all_pending():
            if o.order_id == order_id:
                pos = self._positions.get(o.symbol)
                if pos is None:
                    raise ValueError(f"No position for {o.symbol}")
                transition_position(pos, PositionState.OPEN)
                self._positions.upsert(pos)
                self._pending.resolve(order_id, "filled")
                return pos
        raise ValueError(f"Order {order_id} not found in pending")

    # ── Stop / protection ────────────────────────────────────────

    def place_stop(
        self,
        symbol: str,
        stop_price: float,
        qty: int,
    ) -> PendingOrder:
        """Place a stop-loss order for an open position."""
        pos = self._positions.get(symbol)
        if pos is None or pos.state != PositionState.OPEN:
            raise ValueError(f"Cannot place stop: {symbol} not in OPEN state")

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
        return order, pos

    def confirm_exit_fill(self, order_id: str) -> PositionStateModel:
        """Mark an exit as filled, reduce shares. Close only if zero remain.

        When shares remain after a partial exit, transition back to OPEN
        and place a new stop for the remaining quantity.
        """
        for o in self._pending.all_pending():
            if o.order_id == order_id:
                pos = self._positions.get(o.symbol)
                if pos is not None:
                    remaining = max(pos.current_shares - o.qty, 0)
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
        """Transition an OPEN or EXITING position to UNPROTECTED state.

        EXITING positions that timed out can be escalated to UNPROTECTED
        so the exit engine can handle them.
        """
        pos = self._positions.get(symbol)
        if pos is None:
            raise ValueError(f"No position for {symbol}")
        if pos.state not in (PositionState.OPEN, PositionState.EXITING):
            raise ValueError(
                f"Cannot mark unprotected: {symbol} is {pos.state.value}, not OPEN/EXITING"
            )
        transition_position(pos, PositionState.UNPROTECTED)
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
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
    ) -> None:
        super().__init__(positions=positions, pending_orders=pending_orders)
        self._api_key = api_key or os.getenv("ALPACA_API_KEY")
        self._secret_key = secret_key or os.getenv("ALPACA_SECRET_KEY")
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from alpaca.trading import TradingClient
            self._client = TradingClient(self._api_key, self._secret_key, paper=True)
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
            opened_at=now, updated_at=now,
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
        return order, position

    def confirm_fill(self, order_id: str) -> PositionStateModel:
        """Check Alpaca order status (T6.7). No synthetic fill.

        - filled → advance to OPEN
        - partially_filled → OPEN with filled qty only
        - rejected/canceled/expired → mark ERROR
        - pending → stay PENDING_ENTRY (caller must retry)
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

    # ── Stop / protection ───────────────────────────────────────

    def place_stop(self, symbol: str, stop_price: float, qty: int) -> PendingOrder:
        """Place STOP sell order at Alpaca. T6.5: no synthetic fallback."""
        pos = self._positions.get(symbol)
        if pos is None or pos.state != PositionState.OPEN:
            raise ValueError(f"Cannot place stop: {symbol} not in OPEN state")

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
                        pos.realized_pnl = (avg_price - pos.average_entry) * filled_qty
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
                    return pos

                elif status == "partially_filled":
                    filled_qty = int(float(getattr(alpaca_order, 'filled_qty', None) or 0))
                    if filled_qty > 0:
                        avg_price = float(getattr(alpaca_order, 'filled_avg_price', None) or 0)
                        if avg_price > 0 and pos.average_entry:
                            pos.realized_pnl = (avg_price - pos.average_entry) * filled_qty
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
                        self._pending.resolve(order_id, "partially_filled")
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
                actions.append({
                    "action": "update_qty_reprotect_warning",
                    "symbol": symbol,
                    "reason": f"broker_qty_more:{b_qty}>{old_local_shares}",
                    "qty": b_qty,
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
        positions = gateway.client.get_positions()
        result: dict[str, tuple[int, float]] = {}
        for pos in positions:
            qty = int(float(pos.qty))
            avg_entry = float(getattr(pos, 'avg_entry_price', None) or pos.cost_basis or 0)
            result[pos.symbol] = (qty, avg_entry)
        return result
    except Exception:
        logger.exception("Failed to fetch broker snapshot from Alpaca")
        return None
