"""Phase 7 state-machine tests per SPEC section 13.

Covers:
  - Valid/invalid state transitions.
  - Symbol lock logic.
  - Candidate lifecycle stages.
  - PositionStore CRUD and persistence.
  - PendingOrderStore tracking.
"""

import json
from datetime import datetime, timezone

import pytest

from src.state_machine import (
    PositionStore,
    PendingOrderStore,
    candidate_has_reached,
    candidate_stage_index,
    is_symbol_locked,
    is_symbol_locked_for_entries,
    is_valid_transition,
    transition_position,
)
from src.models.schemas import (
    OrderActionType,
    PendingOrder,
    PositionState,
    PositionStateModel,
)


# ── Helpers ───────────────────────────────────────────────────────


def _pos(symbol: str = "DSY", state: PositionState = PositionState.NONE, **kw) -> PositionStateModel:
    return PositionStateModel(symbol=symbol, state=state, **kw)


# ──────────────────────────────────────────────────────────────────
#  Valid transitions
# ──────────────────────────────────────────────────────────────────


class TestValidTransitions:
    def test_none_to_pending_entry(self):
        assert is_valid_transition(PositionState.NONE, PositionState.PENDING_ENTRY) is True

    def test_none_to_open_invalid(self):
        assert is_valid_transition(PositionState.NONE, PositionState.OPEN) is False

    def test_open_to_adding(self):
        assert is_valid_transition(PositionState.OPEN, PositionState.ADDING) is True

    def test_open_to_scaling_out(self):
        assert is_valid_transition(PositionState.OPEN, PositionState.SCALING_OUT) is True

    def test_open_to_closed(self):
        assert is_valid_transition(PositionState.OPEN, PositionState.CLOSED) is True

    def test_open_to_unprotected(self):
        assert is_valid_transition(PositionState.OPEN, PositionState.UNPROTECTED) is True

    def test_unprotected_to_open(self):
        assert is_valid_transition(PositionState.UNPROTECTED, PositionState.OPEN) is True

    def test_closed_to_none(self):
        assert is_valid_transition(PositionState.CLOSED, PositionState.NONE) is True

    def test_closed_cannot_go_to_open(self):
        assert is_valid_transition(PositionState.CLOSED, PositionState.OPEN) is False

    def test_error_to_closed(self):
        assert is_valid_transition(PositionState.ERROR, PositionState.CLOSED) is True


class TestTransitionPosition:
    def test_valid_transition(self):
        p = _pos(state=PositionState.NONE)
        result = transition_position(p, PositionState.PENDING_ENTRY)
        assert result.state == PositionState.PENDING_ENTRY

    def test_invalid_transition_raises(self):
        p = _pos(state=PositionState.NONE)
        with pytest.raises(ValueError, match="Invalid transition"):
            transition_position(p, PositionState.OPEN)

    def test_force_overrides_validation(self):
        p = _pos(state=PositionState.NONE)
        result = transition_position(p, PositionState.OPEN, force=True)
        assert result.state == PositionState.OPEN

    def test_updates_timestamp(self):
        p = _pos(state=PositionState.NONE)
        before = datetime.now(timezone.utc)
        result = transition_position(p, PositionState.PENDING_ENTRY)
        assert result.updated_at is not None
        assert result.updated_at >= before


# ──────────────────────────────────────────────────────────────────
#  Symbol lock
# ──────────────────────────────────────────────────────────────────


class TestSymbolLock:
    def test_none_not_locked(self):
        p = _pos(state=PositionState.NONE)
        assert is_symbol_locked(p) is False

    def test_closed_not_locked(self):
        p = _pos(state=PositionState.CLOSED)
        assert is_symbol_locked(p) is False

    def test_open_is_locked(self):
        p = _pos(state=PositionState.OPEN)
        assert is_symbol_locked(p) is True

    def test_pending_entry_is_locked(self):
        p = _pos(state=PositionState.PENDING_ENTRY)
        assert is_symbol_locked(p) is True

    def test_exiting_is_locked(self):
        p = _pos(state=PositionState.EXITING)
        assert is_symbol_locked(p) is True

    def test_unprotected_is_locked(self):
        p = _pos(state=PositionState.UNPROTECTED)
        assert is_symbol_locked(p) is True

    def test_error_is_locked(self):
        p = _pos(state=PositionState.ERROR)
        assert is_symbol_locked(p) is True


class TestSymbolLockForEntries:
    def test_no_position_not_locked(self):
        assert is_symbol_locked_for_entries(None) is False

    def test_active_position_is_locked(self):
        p = _pos(state=PositionState.OPEN)
        assert is_symbol_locked_for_entries(p) is True

    def test_pending_buy_locks_even_without_position(self):
        assert is_symbol_locked_for_entries(None, has_pending_buy=True) is True

    def test_daily_loss_capped_locks(self):
        assert is_symbol_locked_for_entries(None, daily_loss_capped=True) is True


# ──────────────────────────────────────────────────────────────────
#  Candidate lifecycle
# ──────────────────────────────────────────────────────────────────


class TestCandidateLifecycle:
    def test_stage_index(self):
        assert candidate_stage_index("DISCOVERED") == 0
        assert candidate_stage_index("STARTER_READY") == 5
        assert candidate_stage_index("unknown") == -1

    def test_has_reached(self):
        assert candidate_has_reached("ENRICHED", "DISCOVERED") is True
        assert candidate_has_reached("DISCOVERED", "ENRICHED") is False

    def test_has_reached_same(self):
        assert candidate_has_reached("WATCHING", "WATCHING") is True

    def test_case_insensitive(self):
        assert candidate_stage_index("discovered") == 0


# ──────────────────────────────────────────────────────────────────
#  PositionStore
# ──────────────────────────────────────────────────────────────────


class TestPositionStore:
    def test_upsert_and_get(self):
        store = PositionStore()
        p = _pos("DSY", PositionState.OPEN)
        store.upsert(p)
        assert store.get("DSY") is not None
        assert store.get("DSY").state == PositionState.OPEN

    def test_upsert_updates_existing(self):
        store = PositionStore()
        store.upsert(_pos("DSY", PositionState.OPEN))
        store.upsert(_pos("DSY", PositionState.CLOSED))
        assert store.get("DSY").state == PositionState.CLOSED

    def test_remove(self):
        store = PositionStore()
        store.upsert(_pos("DSY"))
        store.remove("DSY")
        assert store.get("DSY") is None

    def test_all_open_excludes_closed(self):
        store = PositionStore()
        store.upsert(_pos("A", PositionState.OPEN))
        store.upsert(_pos("B", PositionState.CLOSED))
        store.upsert(_pos("C", PositionState.RUNNER))
        assert len(store.all_open()) == 2

    def test_all_positions(self):
        store = PositionStore()
        store.upsert(_pos("A", PositionState.OPEN))
        store.upsert(_pos("B", PositionState.CLOSED))
        assert len(store.all_positions()) == 2

    def test_locked_symbols(self):
        store = PositionStore()
        store.upsert(_pos("A", PositionState.OPEN))
        store.upsert(_pos("B", PositionState.NONE))
        store.upsert(_pos("C", PositionState.PENDING_ENTRY))
        locked = store.locked_symbols()
        assert "A" in locked
        assert "B" not in locked
        assert "C" in locked

    def test_open_position_count(self):
        store = PositionStore()
        assert store.open_position_count() == 0
        store.upsert(_pos("A", PositionState.OPEN))
        assert store.open_position_count() == 1

    def test_contains(self):
        store = PositionStore()
        store.upsert(_pos("DSY"))
        assert "DSY" in store
        assert "AAPL" not in store

    def test_save_and_load(self, tmp_path):
        store = PositionStore()
        store.upsert(_pos("DSY", PositionState.OPEN, current_shares=50))
        path = tmp_path / "positions.json"
        store.save_to_disk(path)
        assert path.exists()

        loaded = PositionStore.load_from_disk(path)
        assert loaded.get("DSY") is not None
        assert loaded.get("DSY").current_shares == 50

    def test_load_nonexistent(self, tmp_path):
        store = PositionStore.load_from_disk(tmp_path / "nonexistent.json")
        assert len(store) == 0

    def test_to_dict_is_json_serializable(self):
        store = PositionStore()
        store.upsert(_pos("DSY", PositionState.OPEN, current_shares=50, entry_price=10.50))
        d = store.to_dict()
        json.dumps(d)  # should not raise


# ──────────────────────────────────────────────────────────────────
#  PendingOrderStore
# ──────────────────────────────────────────────────────────────────


class TestPendingOrderStore:
    def _order(self, symbol: str = "DSY", oid: str = "o1", otype=OrderActionType.ENTRY) -> PendingOrder:
        return PendingOrder(symbol=symbol, order_id=oid, order_type=otype, side="buy")

    def test_add_and_get(self):
        store = PendingOrderStore()
        store.add(self._order())
        assert len(store.get_for_symbol("DSY")) == 1

    def test_resolve_removes(self):
        store = PendingOrderStore()
        store.add(self._order(oid="o1"))
        store.resolve("o1", "filled")
        assert len(store) == 0

    def test_has_pending_buy(self):
        store = PendingOrderStore()
        store.add(self._order(otype=OrderActionType.ENTRY, oid="buy1"))
        assert store.has_pending_buy("DSY") is True
        assert store.has_pending_buy("AAPL") is False

    def test_has_pending_buy_with_stop(self):
        store = PendingOrderStore()
        store.add(self._order(otype=OrderActionType.STOP, oid="stop1"))
        assert store.has_pending_buy("DSY") is False  # STOP is not a buy

    def test_all_pending(self):
        store = PendingOrderStore()
        store.add(self._order("A", "o1"))
        store.add(self._order("B", "o2"))
        assert len(store.all_pending()) == 2

    def test_contains(self):
        store = PendingOrderStore()
        store.add(self._order(oid="o1"))
        assert "o1" in store
        assert "o2" not in store

    def test_len(self):
        store = PendingOrderStore()
        assert len(store) == 0
        store.add(self._order("A", "o1"))
        store.add(self._order("B", "o2"))
        assert len(store) == 2
