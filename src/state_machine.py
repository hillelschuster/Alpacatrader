"""
Phase 7 — Position state machine per SPEC section 13.

Manages position lifecycle, symbol locks, and lightweight position persistence.
No broker calls.  Pure operational logic.

State transitions (SPEC §13.2):
  NONE → PENDING_ENTRY
  PENDING_ENTRY → OPEN | CLOSED | ERROR
   OPEN → ADDING | RUNNER | EXITING | UNPROTECTED | CLOSED | ERROR
   ADDING → OPEN | RUNNER | ERROR
   RUNNER → ADDING | EXITING | UNPROTECTED | CLOSED | ERROR
  EXITING → CLOSED | ERROR
  UNPROTECTED → OPEN | EXITING | CLOSED | ERROR
  CLOSED → NONE (session re-entry only)
  ERROR → CLOSED | NONE (after manual flatten)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.models.schemas import PositionState, PositionStateModel, PendingOrder


# ──────────────────────────────────────────────────────────────────
#  Valid state transitions
# ──────────────────────────────────────────────────────────────────

_VALID_TRANSITIONS: dict[PositionState, set[PositionState]] = {
    PositionState.NONE: {PositionState.PENDING_ENTRY},
    # ponytail: PENDING_ENTRY → UNPROTECTED for mark_unprotected on protect failure
    PositionState.PENDING_ENTRY: {
        PositionState.OPEN, PositionState.CLOSED, PositionState.ERROR, PositionState.UNPROTECTED,
    },
    PositionState.OPEN: {
        PositionState.ADDING, PositionState.RUNNER,
        PositionState.EXITING, PositionState.UNPROTECTED, PositionState.CLOSED,
        PositionState.ERROR,
    },
    PositionState.ADDING: {PositionState.OPEN, PositionState.RUNNER, PositionState.ERROR},
    PositionState.RUNNER: {
        PositionState.ADDING, PositionState.EXITING,
        PositionState.UNPROTECTED, PositionState.CLOSED, PositionState.ERROR,
    },
    # ponytail: EXITING → UNPROTECTED for mark_unprotected on exit/protect failure
    PositionState.EXITING: {
        PositionState.CLOSED, PositionState.ERROR, PositionState.UNPROTECTED,
    },
    PositionState.UNPROTECTED: {
        PositionState.OPEN, PositionState.EXITING, PositionState.CLOSED, PositionState.ERROR,
    },
    PositionState.CLOSED: {PositionState.NONE},
    PositionState.ERROR: {PositionState.CLOSED, PositionState.NONE},
}

# States that lock the symbol from new entries (SPEC §13.3)
_LOCKED_STATES: set[PositionState] = {
    PositionState.PENDING_ENTRY,
    PositionState.OPEN,
    PositionState.ADDING,
    PositionState.RUNNER,
    PositionState.EXITING,
    PositionState.UNPROTECTED,
    PositionState.ERROR,
}


def is_valid_transition(from_state: PositionState, to_state: PositionState) -> bool:
    """Return True if the transition is allowed."""
    return to_state in _VALID_TRANSITIONS.get(from_state, set())


def transition_position(
    position: PositionStateModel,
    new_state: PositionState,
    *,
    force: bool = False,
) -> PositionStateModel:
    """Apply a state transition to a position.

    Raises ValueError if the transition is not allowed (unless ``force=True``).
    Updates ``updated_at`` automatically.
    """
    if not force and not is_valid_transition(position.state, new_state):
        raise ValueError(
            f"Invalid transition: {position.state.value} → {new_state.value}"
        )
    position.state = new_state
    position.updated_at = datetime.now(timezone.utc)
    return position


def is_symbol_locked(position: PositionStateModel) -> bool:
    """Check if a position's state locks the symbol from new entries."""
    return position.state in _LOCKED_STATES


def is_symbol_locked_for_entries(
    position: Optional[PositionStateModel],
    *,
    has_pending_buy: bool = False,
    daily_loss_capped: bool = False,
) -> bool:
    """Full symbol-lock check per SPEC §13.3.

    A symbol is locked when any are true:
      - position state is not NONE/CLOSED
      - pending buy order exists
      - exit is in progress
      - symbol hit per-symbol daily loss cap
    """
    if daily_loss_capped:
        return True
    if position is None:
        return has_pending_buy
    if position.state not in (PositionState.NONE, PositionState.CLOSED):
        return True
    if has_pending_buy:
        return True
    if position.state == PositionState.EXITING:
        return True
    return False


# ──────────────────────────────────────────────────────────────────
#  Candidate lifecycle (SPEC §13.1)
# ──────────────────────────────────────────────────────────────────

# Reserved for future runtime wiring of lifecycle-aware pipelines.
# Currently unused in runtime (T7.3) — tested and kept as spec artifacts.
# Once candidate enrichment and state tracking are wired end-to-end
# these helpers govern when a candidate has progressed far enough
# for a given downstream stage (e.g., only STARTER_READY candidates
# should reach entry sizing).

CANDIDATE_LIFECYCLE = [
    "DISCOVERED",
    "ATTENTION_RANKED",
    "ENRICHED",
    "HARD_CHECKED",
    "WATCHING",
    "STARTER_READY",
]


def candidate_stage_index(stage: str) -> int:
    """Return the ordinal position of a candidate lifecycle stage."""
    try:
        return CANDIDATE_LIFECYCLE.index(stage.upper())
    except ValueError:
        return -1


def candidate_has_reached(candidate_stage: str, target: str) -> bool:
    """Return True if candidate has reached or passed ``target`` stage."""
    return candidate_stage_index(candidate_stage) >= candidate_stage_index(target)


# ──────────────────────────────────────────────────────────────────
#  Position store — in-memory + optional JSON persistence
# ──────────────────────────────────────────────────────────────────


class PositionStore:
    """Lightweight position-state store with optional JSON persistence.

    Uses in-memory dict for speed.  ``save_to_disk()`` / ``load_from_disk()``
    provide crash-recovery hooks.
    """

    def __init__(self) -> None:
        self._positions: dict[str, PositionStateModel] = {}

    # ── CRUD ────────────────────────────────────────────────────

    def upsert(self, position: PositionStateModel) -> None:
        """Insert or update a position."""
        position.updated_at = datetime.now(timezone.utc)
        self._positions[position.symbol] = position

    def get(self, symbol: str) -> Optional[PositionStateModel]:
        """Get a position by symbol, or None."""
        return self._positions.get(symbol)

    def remove(self, symbol: str) -> None:
        """Remove a position from the store."""
        self._positions.pop(symbol, None)

    def all_open(self) -> list[PositionStateModel]:
        """Return all non-terminal positions."""
        terminal = {PositionState.NONE, PositionState.CLOSED, PositionState.ERROR}
        return [p for p in self._positions.values() if p.state not in terminal]

    def all_positions(self) -> list[PositionStateModel]:
        """Return every stored position."""
        return list(self._positions.values())

    def locked_symbols(self) -> set[str]:
        """Return set of symbols currently locked from new entries."""
        return {s for s, p in self._positions.items() if is_symbol_locked(p)}

    def open_position_count(self) -> int:
        """Count of positions with risk (not NONE/CLOSED)."""
        return len(self.all_open())

    def __len__(self) -> int:
        return len(self._positions)

    def __contains__(self, symbol: str) -> bool:
        return symbol in self._positions

    # ── Persistence ────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Serialize all positions to a JSON-safe dict."""
        return {
            sym: pos.model_dump(mode="json")
            for sym, pos in self._positions.items()
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PositionStore":
        """Restore from a serialised dict."""
        store = cls()
        for sym, raw in data.items():
            store._positions[sym] = PositionStateModel(**raw)
        return store

    def save_to_disk(self, path: str | Path) -> None:
        """Persist all positions to a JSON file."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), default=str, indent=2))

    @classmethod
    def load_from_disk(cls, path: str | Path) -> "PositionStore":
        """Restore positions from a JSON file. Returns empty store if file missing."""
        p = Path(path)
        if not p.exists():
            return cls()
        data = json.loads(p.read_text())
        return cls.from_dict(data)


# ──────────────────────────────────────────────────────────────────
#  Pending-order store
# ──────────────────────────────────────────────────────────────────


class PendingOrderStore:
    """Tracks submitted but unresolved orders (SPEC §13.4).

    Prevents duplicate entries, double-sells, and missing-protection errors.
    """

    def __init__(self) -> None:
        self._orders: dict[str, list[PendingOrder]] = {}  # symbol → orders

    def add(self, order: PendingOrder) -> None:
        """Record a new pending order."""
        self._orders.setdefault(order.symbol, []).append(order)

    def get_for_symbol(self, symbol: str) -> list[PendingOrder]:
        """Return all pending orders for a symbol."""
        return self._orders.get(symbol, [])

    def resolve(self, order_id: str, status: str = "filled") -> None:
        """Mark an order as resolved (filled/cancelled/rejected)."""
        for sym, orders in self._orders.items():
            for o in orders:
                if o.order_id == order_id:
                    orders.remove(o)
                    if not orders:
                        del self._orders[sym]
                    return

    def has_pending_buy(self, symbol: str) -> bool:
        """Check if a buy/entry order is pending for the symbol."""
        for o in self.get_for_symbol(symbol):
            if o.order_type.value in ("entry", "add") and o.side == "buy":
                return True
        return False

    def all_pending(self) -> list[PendingOrder]:
        """Return all unresolved orders."""
        result: list[PendingOrder] = []
        for orders in self._orders.values():
            result.extend(orders)
        return result

    def __len__(self) -> int:
        return sum(len(v) for v in self._orders.values())

    def __contains__(self, order_id: str) -> bool:
        for orders in self._orders.values():
            for o in orders:
                if o.order_id == order_id:
                    return True
        return False
