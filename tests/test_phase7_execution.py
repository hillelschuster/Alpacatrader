"""Phase 7 execution-gateway & reconciliation tests per SPEC §14-15.

Verifies:
  - Paper entry submission creates PENDING_ENTRY state + pending order.
  - Duplicate entries are blocked (symbol lock).
  - Stop placement and protection.
  - Exit submission and fill confirmation.
  - Unprotected position detection.
  - Reconciliation handles all 8 SPEC cases.
"""

import pytest

from src.paper_execution import PaperExecutionGateway, reconcile_positions
from src.state_machine import PositionStore, PendingOrderStore
from src.models.schemas import (
    EntrySetupType,
    EntrySignal,
    OrderActionType,
    PositionState,
    PositionStateModel,
)


# ── Helpers ───────────────────────────────────────────────────────


def _signal(symbol: str = "DSY", entry: float = 10.50, stop: float = 10.30, shares: int = 50) -> EntrySignal:
    return EntrySignal(
        symbol=symbol,
        entry_setup=EntrySetupType.FIRST_PULLBACK,
        entry_price=entry,
        stop_price=stop,
        risk_per_share=abs(entry - stop),
        target_price=entry + 2 * abs(entry - stop),
        proposed_shares=shares,
        risk_amount=abs(entry - stop) * shares,
        invalidation="test",
    )


# ──────────────────────────────────────────────────────────────────
#  Entry submission
# ──────────────────────────────────────────────────────────────────


class TestSubmitEntry:
    def test_creates_position_and_order(self):
        gw = PaperExecutionGateway()
        order, pos = gw.submit_entry(_signal())
        assert pos.state == PositionState.PENDING_ENTRY
        assert pos.symbol == "DSY"
        assert order.order_type == OrderActionType.ENTRY
        assert order.side == "buy"

    def test_symbol_is_locked_after_submit(self):
        gw = PaperExecutionGateway()
        gw.submit_entry(_signal())
        assert gw.is_symbol_locked("DSY") is True

    def test_duplicate_entry_blocked(self):
        gw = PaperExecutionGateway()
        gw.submit_entry(_signal())
        with pytest.raises(ValueError, match="already has active position"):
            gw.submit_entry(_signal())

    def test_fill_confirmation_advances_to_open(self):
        gw = PaperExecutionGateway()
        order, _ = gw.submit_entry(_signal())
        pos = gw.confirm_fill(order.order_id)
        assert pos.state == PositionState.OPEN

    def test_pending_order_cleared_after_fill(self):
        gw = PaperExecutionGateway()
        order, _ = gw.submit_entry(_signal())
        gw.confirm_fill(order.order_id)
        assert len(gw.pending) == 0

    def test_order_qty_matches_signal_proposed_shares(self):
        """Submitted order qty must match the signal's proposed_shares."""
        gw = PaperExecutionGateway()
        order, pos = gw.submit_entry(_signal(shares=75))
        assert order.qty == 75
        assert pos.current_shares == 75


# ──────────────────────────────────────────────────────────────────
#  Stop / protection
# ──────────────────────────────────────────────────────────────────


class TestStopProtection:
    def test_place_stop_on_open_position(self):
        gw = PaperExecutionGateway()
        order, _ = gw.submit_entry(_signal())
        gw.confirm_fill(order.order_id)
        stop = gw.place_stop("DSY", 10.20, 50)
        assert stop is not None
        assert stop.order_type == OrderActionType.STOP
        assert gw.positions.get("DSY").stop_price == 10.20

    def test_place_stop_on_non_open_raises(self):
        gw = PaperExecutionGateway()
        with pytest.raises(ValueError, match="not in OPEN"):
            gw.place_stop("DSY", 10.0, 50)

    def test_protect_position_idempotent(self):
        gw = PaperExecutionGateway()
        order, _ = gw.submit_entry(_signal())
        gw.confirm_fill(order.order_id)
        gw.place_stop("DSY", 10.20, 50)
        # Second call with same stop should be idempotent
        result = gw.protect_position("DSY", 10.20)
        assert result is None  # already protected

    def test_unprotected_detection(self):
        gw = PaperExecutionGateway()
        order, _ = gw.submit_entry(_signal())
        gw.confirm_fill(order.order_id)
        # No stop placed yet
        unprotected = gw.get_unprotected_positions()
        assert "DSY" in unprotected

    def test_protected_not_in_unprotected(self):
        gw = PaperExecutionGateway()
        order, _ = gw.submit_entry(_signal())
        gw.confirm_fill(order.order_id)
        gw.place_stop("DSY", 10.20, 50)
        assert "DSY" not in gw.get_unprotected_positions()

    def test_mark_unprotected_from_open(self):
        """Marking an OPEN position as UNPROTECTED transitions state."""
        gw = PaperExecutionGateway()
        order, _ = gw.submit_entry(_signal())
        gw.confirm_fill(order.order_id)
        pos = gw.mark_unprotected("DSY")
        assert pos.state == PositionState.UNPROTECTED

    def test_mark_unprotected_shows_in_unprotected_list(self):
        """An UNPROTECTED position is listed by get_unprotected_positions."""
        gw = PaperExecutionGateway()
        order, _ = gw.submit_entry(_signal())
        gw.confirm_fill(order.order_id)
        gw.mark_unprotected("DSY")
        assert "DSY" in gw.get_unprotected_positions()

    def test_mark_unprotected_on_non_open_raises(self):
        """mark_unprotected on PENDING_ENTRY raises ValueError."""
        gw = PaperExecutionGateway()
        gw.submit_entry(_signal())
        with pytest.raises(ValueError, match="not OPEN"):
            gw.mark_unprotected("DSY")

    def test_mark_unprotected_nonexistent_raises(self):
        """mark_unprotected on nonexistent symbol raises ValueError."""
        gw = PaperExecutionGateway()
        with pytest.raises(ValueError, match="No position"):
            gw.mark_unprotected("NONEXISTENT")


# ──────────────────────────────────────────────────────────────────
#  Cancel
# ──────────────────────────────────────────────────────────────────


class TestCancel:
    def test_cancel_order(self):
        gw = PaperExecutionGateway()
        order, _ = gw.submit_entry(_signal())
        assert gw.cancel_order(order.order_id) is True
        assert len(gw.pending) == 0

    def test_cancel_nonexistent(self):
        gw = PaperExecutionGateway()
        assert gw.cancel_order("nonexistent") is False

    def test_cancel_stale_orders(self):
        gw = PaperExecutionGateway()
        for i in range(3):
            gw.submit_entry(_signal(symbol=f"S{i}"))
        gw.submit_entry(_signal(symbol="DSY"))
        cancelled = gw.cancel_stale_orders("DSY")
        assert cancelled == 1


# ──────────────────────────────────────────────────────────────────
#  Exit
# ──────────────────────────────────────────────────────────────────


class TestExit:
    def test_submit_exit(self):
        gw = PaperExecutionGateway()
        order, _ = gw.submit_entry(_signal())
        gw.confirm_fill(order.order_id)
        gw.place_stop("DSY", 10.20, 50)

        exit_order, pos = gw.submit_exit("DSY", "target hit")
        assert exit_order.order_type == OrderActionType.EXIT
        assert pos.state == PositionState.EXITING

    def test_confirm_exit_fill_closes_position(self):
        gw = PaperExecutionGateway()
        order, _ = gw.submit_entry(_signal())
        gw.confirm_fill(order.order_id)
        exit_order, _ = gw.submit_exit("DSY", "target hit")
        pos = gw.confirm_exit_fill(exit_order.order_id)
        assert pos.state == PositionState.CLOSED
        assert pos.current_shares == 0

    def test_exit_on_nonexistent_raises(self):
        gw = PaperExecutionGateway()
        with pytest.raises(ValueError, match="No position"):
            gw.submit_exit("NONEXISTENT", "test")


# ──────────────────────────────────────────────────────────────────
#  Reconcilation — all 8 cases
# ──────────────────────────────────────────────────────────────────


class TestReconciliation:
    def test_case1_broker_has_local_none(self):
        """Case 1: broker has position, local has none → insert + protect."""
        local = PositionStore()
        pending = PendingOrderStore()
        broker = {"DSY": (50, 10.50)}

        actions = reconcile_positions(
            broker_positions=broker, local_store=local, pending_store=pending,
        )
        assert any(a["action"] == "insert_protect" for a in actions)
        assert local.get("DSY") is not None

    def test_case2_qty_matches(self):
        """Case 2: broker qty matches local → verify stop."""
        local = PositionStore()
        local.upsert(PositionStateModel(symbol="DSY", state=PositionState.OPEN, current_shares=50))
        pending = PendingOrderStore()
        broker = {"DSY": (50, 10.50)}

        actions = reconcile_positions(
            broker_positions=broker, local_store=local, pending_store=pending,
        )
        assert any(a["action"] == "verify_stop" for a in actions)

    def test_case3_broker_qty_less(self):
        """Case 3: broker qty < local → partial fill, update local."""
        local = PositionStore()
        local.upsert(PositionStateModel(symbol="DSY", state=PositionState.OPEN, current_shares=100))
        pending = PendingOrderStore()
        broker = {"DSY": (50, 10.50)}

        actions = reconcile_positions(
            broker_positions=broker, local_store=local, pending_store=pending,
        )
        assert any(a["action"] == "update_qty_reprotect" for a in actions)
        assert local.get("DSY").current_shares == 50

    def test_case4_broker_qty_more(self):
        """Case 4: broker qty > local → missed fill, update local."""
        local = PositionStore()
        local.upsert(PositionStateModel(symbol="DSY", state=PositionState.OPEN, current_shares=50))
        pending = PendingOrderStore()
        broker = {"DSY": (100, 10.50)}

        actions = reconcile_positions(
            broker_positions=broker, local_store=local, pending_store=pending,
        )
        assert any(a["action"] == "update_qty_reprotect_warning" for a in actions)
        assert local.get("DSY").current_shares == 100

    def test_case5_broker_none_local_active(self):
        """Case 5: broker has no position, local is active → close local."""
        local = PositionStore()
        local.upsert(PositionStateModel(symbol="DSY", state=PositionState.OPEN, current_shares=50))
        pending = PendingOrderStore()
        broker: dict = {}

        actions = reconcile_positions(
            broker_positions=broker, local_store=local, pending_store=pending,
        )
        assert any(a["action"] == "close_local" for a in actions)
        assert local.get("DSY").state == PositionState.CLOSED

    def test_case6_stale_orders_cancelled(self):
        """Case 6: broker no position, local has pending orders → cancel."""
        local = PositionStore()
        local.upsert(PositionStateModel(symbol="DSY", state=PositionState.CLOSED))
        pending = PendingOrderStore()
        from src.models.schemas import PendingOrder as PO
        pending.add(PO(symbol="DSY", order_id="stale1", order_type=OrderActionType.ENTRY, side="buy"))
        broker: dict = {}

        actions = reconcile_positions(
            broker_positions=broker, local_store=local, pending_store=pending,
        )
        assert any(a["action"] == "cancel_stale_order" for a in actions)
        assert len(pending) == 0

    def test_case7_irreconcilable(self):
        """Case 7: broker qty 0, local active → ERROR."""
        local = PositionStore()
        local.upsert(PositionStateModel(symbol="DSY", state=PositionState.OPEN, current_shares=50))
        pending = PendingOrderStore()
        broker = {"DSY": (0, 0.0)}

        actions = reconcile_positions(
            broker_positions=broker, local_store=local, pending_store=pending,
        )
        assert any(a["action"] == "irreconcilable" for a in actions)
        assert local.get("DSY").state == PositionState.ERROR

    def test_empty_both_returns_no_actions(self):
        local = PositionStore()
        pending = PendingOrderStore()
        broker: dict = {}
        actions = reconcile_positions(
            broker_positions=broker, local_store=local, pending_store=pending,
        )
        assert actions == []
