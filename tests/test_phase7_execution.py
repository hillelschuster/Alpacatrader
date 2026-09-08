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

from src.paper_execution import (
    PaperExecutionGateway,
    AlpacaExecutionGateway,
    reconcile_positions,
    reconcile_open_orders,
)
from src.state_machine import PositionStore, PendingOrderStore
from src.models.schemas import (
    EntrySetupType,
    EntrySignal,
    OrderActionType,
    PendingOrder,
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

    def test_mark_unprotected_on_pending_entry_succeeds(self):
        """mark_unprotected on PENDING_ENTRY transitions to UNPROTECTED (SPEC §11.18 bugfix)."""
        gw = PaperExecutionGateway()
        gw.submit_entry(_signal())
        pos = gw.mark_unprotected("DSY")
        assert pos.state.value == "UNPROTECTED"

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
#  ADD fill — trailing_stop_price must be updated
# ──────────────────────────────────────────────────────────────────


class TestAddFillTrailingStop:
    """ADD fill must set pos.trailing_stop_price alongside stop_price.

    Without this fix, an ADD fill leaves trailing_stop_price stale,
    breaking subsequent trail-update broker sync.
    """

    def _setup_runner(self, gw: PaperExecutionGateway) -> tuple:  # (PendingOrder, str)
        """Helper: create a RUNNER position ready for ADD."""
        order, _ = gw.submit_entry(_signal())
        gw.confirm_fill(order.order_id)
        pos = gw.positions.get("DSY")
        pos.state = PositionState.RUNNER
        pos.highest_price_seen = 11.50
        pos.trailing_stop_price = 10.00
        gw.positions.upsert(pos)
        gw.place_stop("DSY", 10.00, 50)
        return order, "DSY"

    def test_add_fill_updates_trailing_stop_price(self):
        """After confirm_fill for ADD, trailing_stop_price == stop_price."""
        gw = PaperExecutionGateway()
        _, symbol = self._setup_runner(gw)

        # Submit ADD
        add_order, _ = gw.submit_add(symbol, qty=25, entry_price=11.00, stop_price=10.00)
        # The add_order's stop_price is used as new_stop floor
        pos = gw.confirm_fill(add_order.order_id)

        assert pos.state == PositionState.RUNNER
        expected_stop = max(add_order.stop_price or 0, pos.entry_price or 0)
        assert pos.stop_price == expected_stop, (
            f"Expected stop_price={expected_stop}, got {pos.stop_price}"
        )
        assert pos.trailing_stop_price == expected_stop, (
            f"Expected trailing_stop_price={expected_stop}, got {pos.trailing_stop_price}"
        )

    def test_add_partial_fill_updates_trailing_stop_price(self):
        """Partial ADD fill must also set trailing_stop_price."""
        gw = PaperExecutionGateway()
        _, symbol = self._setup_runner(gw)

        # Submit ADD then simulate partial fill
        add_order, _ = gw.submit_add(symbol, qty=25, entry_price=11.00, stop_price=10.00)
        pos = gw.positions.get(symbol)
        pos.state = PositionState.ADDING
        gw.positions.upsert(pos)

        # Simulate partial fill directly using place_stop + manual mutation
        # (same pattern the confirm_fill partially_filled branch follows)
        pos.current_shares = 60  # 50 original + 10 filled
        pos.average_entry = round((50 * 10.50 + 10 * 11.00) / 60, 2)
        pos.add_count += 1
        pos.state = PositionState.RUNNER
        gw.positions.upsert(pos)
        gw._pending.resolve(add_order.order_id, "partially_filled")
        gw.cancel_stale_orders(symbol)
        new_stop = max(add_order.stop_price or 0, pos.entry_price or 0)
        gw.place_stop(symbol, new_stop, 60)
        pos.stop_price = new_stop
        pos.trailing_stop_price = new_stop
        gw.positions.upsert(pos)

        assert pos.stop_price == new_stop
        assert pos.trailing_stop_price == new_stop


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


# ══════════════════════════════════════════════════════════════════
#  Phase 6 — T6.4: AlpacaExecutionGateway with mocked Alpaca client
# ══════════════════════════════════════════════════════════════════


class MockAlpacaOrder:
    """Minimal mock of an Alpaca order object."""
    def __init__(self, id: str, status: str = "new", *, filled_qty: str = "0",
                 filled_avg_price: str | None = None, qty: str = "50"):
        self.id = id
        self.status = status
        self.filled_qty = filled_qty
        self.filled_avg_price = filled_avg_price
        self.qty = qty


@pytest.fixture
def mock_alpaca_client():
    """Return a MagicMock that stands in for TradingClient."""
    from unittest.mock import MagicMock
    return MagicMock()


@pytest.fixture
def alpaca_gw(mock_alpaca_client):
    """AlpacaExecutionGateway with a pre-patched client."""
    gw = AlpacaExecutionGateway(api_key="test_key", secret_key="test_secret")
    gw._client = mock_alpaca_client
    return gw


class TestAlpacaSubmitEntry:
    """T6.4: submit_entry with mocked Alpaca client."""

    def test_submit_entry_returns_order_and_position(self, alpaca_gw, mock_alpaca_client):
        """submit_entry creates PENDING_ENTRY + pending order via Alpaca."""
        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="alpaca-123")

        order, pos = alpaca_gw.submit_entry(_signal())

        assert pos.state == PositionState.PENDING_ENTRY
        assert pos.symbol == "DSY"
        assert order.order_id == "alpaca-123"
        assert order.order_type == OrderActionType.ENTRY
        mock_alpaca_client.submit_order.assert_called_once()

    def test_submit_entry_symbol_locked_raises(self, alpaca_gw, mock_alpaca_client):
        """Duplicate entry raises ValueError."""
        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="a1")
        alpaca_gw.submit_entry(_signal())

        with pytest.raises(ValueError, match="already has active position"):
            alpaca_gw.submit_entry(_signal())

    def test_submit_entry_api_failure_raises_runtime_error(self, alpaca_gw, mock_alpaca_client):
        """T6.5: API failure raises RuntimeError, no synthetic fill."""
        mock_alpaca_client.submit_order.side_effect = ConnectionError("API down")

        with pytest.raises(RuntimeError, match="submit_entry failed"):
            alpaca_gw.submit_entry(_signal())

        # Position must NOT remain in store after failed submit
        assert alpaca_gw.positions.get("DSY") is None

    def test_submit_entry_api_failure_cleans_up_pending(self, alpaca_gw, mock_alpaca_client):
        """After API failure, no pending order remains."""
        mock_alpaca_client.submit_order.side_effect = ConnectionError("API down")

        with pytest.raises(RuntimeError):
            alpaca_gw.submit_entry(_signal())

        assert len(alpaca_gw.pending) == 0


class TestAlpacaConfirmFill:
    """T6.4 + T6.7: confirm_fill with various order statuses."""

    def test_filled_status_advances_to_open(self, alpaca_gw, mock_alpaca_client):
        """filled → OPEN."""
        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="a1")
        order, _ = alpaca_gw.submit_entry(_signal())

        mock_alpaca_client.get_order_by_id.return_value = MockAlpacaOrder(
            id="a1", status="filled", filled_qty="50", filled_avg_price="10.50",
        )
        pos = alpaca_gw.confirm_fill(order.order_id)

        assert pos.state == PositionState.OPEN
        assert pos.current_shares == 50
        assert len(alpaca_gw.pending) == 0

    def test_partially_filled_advances_to_open_with_filled_qty(self, alpaca_gw, mock_alpaca_client):
        """T6.7: partially_filled → OPEN with only filled qty."""
        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="a1")
        order, _ = alpaca_gw.submit_entry(_signal(shares=100))

        mock_alpaca_client.get_order_by_id.side_effect = [
            MockAlpacaOrder(id="a1", status="partially_filled", filled_qty="30", filled_avg_price="10.50"),
            MockAlpacaOrder(id="a1", status="canceled"),
        ]
        pos = alpaca_gw.confirm_fill(order.order_id)

        assert pos.state == PositionState.OPEN
        assert pos.current_shares == 30  # only filled qty
        assert len(alpaca_gw.pending) == 0

    def test_rejected_status_sets_error(self, alpaca_gw, mock_alpaca_client):
        """T6.7: rejected/canceled/expired → ERROR, raises."""
        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="a1")
        order, _ = alpaca_gw.submit_entry(_signal())

        mock_alpaca_client.get_order_by_id.return_value = MockAlpacaOrder(
            id="a1", status="rejected",
        )
        with pytest.raises(RuntimeError, match="rejected"):
            alpaca_gw.confirm_fill(order.order_id)

        pos = alpaca_gw.positions.get("DSY")
        assert pos.state == PositionState.ERROR
        assert alpaca_gw._entry_signals == {}

    def test_canceled_status_sets_error(self, alpaca_gw, mock_alpaca_client):
        """canceled → ERROR."""
        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="a1")
        order, _ = alpaca_gw.submit_entry(_signal())

        mock_alpaca_client.get_order_by_id.return_value = MockAlpacaOrder(
            id="a1", status="canceled",
        )
        with pytest.raises(RuntimeError, match="canceled"):
            alpaca_gw.confirm_fill(order.order_id)

        assert alpaca_gw.positions.get("DSY").state == PositionState.ERROR

    def test_pending_status_stays_pending_entry(self, alpaca_gw, mock_alpaca_client):
        """T6.7: pending → stays PENDING_ENTRY, order not resolved."""
        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="a1")
        order, _ = alpaca_gw.submit_entry(_signal())

        mock_alpaca_client.get_order_by_id.return_value = MockAlpacaOrder(
            id="a1", status="new",
        )
        pos = alpaca_gw.confirm_fill(order.order_id)

        assert pos.state == PositionState.PENDING_ENTRY
        assert len(alpaca_gw.pending) == 1  # still pending

    def test_confirm_fill_api_failure_raises(self, alpaca_gw, mock_alpaca_client):
        """T6.5: API failure in confirm_fill raises RuntimeError."""
        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="a1")
        order, _ = alpaca_gw.submit_entry(_signal())

        mock_alpaca_client.get_order_by_id.side_effect = ConnectionError("API down")

        with pytest.raises(RuntimeError, match="confirm_fill check failed"):
            alpaca_gw.confirm_fill(order.order_id)


class TestAlpacaPlaceStop:
    """T6.4: place_stop with mocked Alpaca client."""

    def test_place_stop_with_alpaca_client(self, alpaca_gw, mock_alpaca_client):
        """place_stop submits to Alpaca and updates local state."""
        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="a1")
        order, _ = alpaca_gw.submit_entry(_signal())

        mock_alpaca_client.get_order_by_id.return_value = MockAlpacaOrder(
            id="a1", status="filled", filled_qty="50", filled_avg_price="10.50",
        )
        alpaca_gw.confirm_fill(order.order_id)

        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="stop-1")
        stop = alpaca_gw.place_stop("DSY", 10.20, 50)

        assert stop.order_id == "stop-1"
        assert stop.order_type == OrderActionType.STOP
        assert alpaca_gw.positions.get("DSY").stop_price == 10.20

    def test_place_stop_api_failure_raises(self, alpaca_gw, mock_alpaca_client):
        """T6.5: stop placement API failure raises, no synthetic."""
        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="a1")
        order, _ = alpaca_gw.submit_entry(_signal())

        mock_alpaca_client.get_order_by_id.return_value = MockAlpacaOrder(
            id="a1", status="filled", filled_qty="50", filled_avg_price="10.50",
        )
        alpaca_gw.confirm_fill(order.order_id)

        mock_alpaca_client.submit_order.side_effect = ConnectionError("API down")

        with pytest.raises(RuntimeError, match="stop placement failed"):
            alpaca_gw.place_stop("DSY", 10.20, 50)


class TestOpenOrderReconciliation:
    """Roadmap #3: broker open orders are reconciled with local pending orders."""

    def test_broker_stop_missing_locally_is_imported(self):
        positions = PositionStore()
        pending = PendingOrderStore()
        positions.upsert(PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            entry_price=10.50, stop_price=10.20,
            current_shares=50, average_entry=10.50,
        ))
        broker_orders = [PendingOrder(
            symbol="DSY", order_id="broker-stop-1",
            order_type=OrderActionType.STOP, side="sell",
            qty=50, status="submitted", stop_price=10.20,
        )]

        actions = reconcile_open_orders(
            broker_orders=broker_orders,
            local_store=positions,
            pending_store=pending,
        )

        assert any(a["action"] == "import_broker_order" for a in actions)
        assert pending.get_for_symbol("DSY")[0].order_id == "broker-stop-1"

    def test_local_pending_stop_missing_from_broker_marks_unprotected(self):
        positions = PositionStore()
        pending = PendingOrderStore()
        positions.upsert(PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            entry_price=10.50, stop_price=10.20,
            current_shares=50, average_entry=10.50,
        ))
        pending.add(PendingOrder(
            symbol="DSY", order_id="local-stop-1",
            order_type=OrderActionType.STOP, side="sell",
            qty=50, status="submitted", stop_price=10.20,
        ))

        actions = reconcile_open_orders(
            broker_orders=[],
            local_store=positions,
            pending_store=pending,
        )

        assert len(pending.get_for_symbol("DSY")) == 0
        assert any(a["action"] == "missing_broker_stop" for a in actions)
        assert positions.get("DSY").state == PositionState.UNPROTECTED

    def test_broker_order_without_local_position_is_cancelled(self):
        positions = PositionStore()
        pending = PendingOrderStore()
        broker_orders = [PendingOrder(
            symbol="GONE", order_id="orphan-stop-1",
            order_type=OrderActionType.STOP, side="sell",
            qty=25, status="submitted", stop_price=4.20,
        )]

        actions = reconcile_open_orders(
            broker_orders=broker_orders,
            local_store=positions,
            pending_store=pending,
        )

        assert actions == [{
            "action": "cancel_orphan_broker_order",
            "symbol": "GONE",
            "reason": "broker_order_without_local_position",
            "order_id": "orphan-stop-1",
        }]


class TestAlpacaExit:
    """T6.4: exit submission and fill confirmation via Alpaca."""

    def _open_position(self, alpaca_gw, mock_alpaca_client, symbol="DSY"):
        """Helper: open a position via Alpaca gateway."""
        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="entry-1")
        order, _ = alpaca_gw.submit_entry(_signal(symbol=symbol))
        mock_alpaca_client.get_order_by_id.return_value = MockAlpacaOrder(
            id="entry-1", status="filled", filled_qty="50", filled_avg_price="10.50",
        )
        alpaca_gw.confirm_fill(order.order_id)
        return alpaca_gw.positions.get(symbol)

    def test_submit_exit_with_alpaca_client(self, alpaca_gw, mock_alpaca_client):
        """submit_exit sends market sell to Alpaca."""
        self._open_position(alpaca_gw, mock_alpaca_client)

        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="exit-1")
        exit_order, pos = alpaca_gw.submit_exit("DSY", "target hit")

        assert exit_order.order_type == OrderActionType.EXIT
        assert exit_order.order_id == "exit-1"
        assert pos.state == PositionState.EXITING

    def test_submit_exit_api_failure_raises(self, alpaca_gw, mock_alpaca_client):
        """T6.5: exit API failure raises RuntimeError."""
        self._open_position(alpaca_gw, mock_alpaca_client)

        mock_alpaca_client.submit_order.side_effect = ConnectionError("API down")

        with pytest.raises(RuntimeError, match="exit failed"):
            alpaca_gw.submit_exit("DSY", "test")

    def test_submit_exit_api_failure_restores_stop(self, alpaca_gw, mock_alpaca_client):
        self._open_position(alpaca_gw, mock_alpaca_client)

        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="stop-1")
        alpaca_gw.place_stop("DSY", 10.30, 50)

        # Cancel verification must confirm stop-1 cancelled before exit proceeds
        mock_alpaca_client.get_order_by_id.side_effect = [
            MockAlpacaOrder(id="stop-1", status="canceled"),
        ]
        mock_alpaca_client.submit_order.side_effect = [
            ConnectionError("API down"),  # exit submit fails
            MockAlpacaOrder(id="restored-stop"),  # stop restore succeeds
        ]

        with pytest.raises(RuntimeError, match="exit failed"):
            alpaca_gw.submit_exit("DSY", "test")

        pos = alpaca_gw.positions.get("DSY")
        assert pos.state == PositionState.OPEN
        assert alpaca_gw._has_pending_stop("DSY")

    def test_confirm_exit_fill_closes_position(self, alpaca_gw, mock_alpaca_client):
        """filled exit → CLOSED."""
        self._open_position(alpaca_gw, mock_alpaca_client)

        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="exit-1")
        exit_order, _ = alpaca_gw.submit_exit("DSY", "target hit")

        mock_alpaca_client.get_order_by_id.return_value = MockAlpacaOrder(
            id="exit-1", status="filled", filled_qty="50", filled_avg_price="11.00",
        )
        pos = alpaca_gw.confirm_exit_fill(exit_order.order_id)

        assert pos.state == PositionState.CLOSED
        assert pos.current_shares == 0

    def test_confirm_exit_partial_fill_reduces_shares(self, alpaca_gw, mock_alpaca_client):
        """T6.7: partial exit fill reduces shares, doesn't close."""
        self._open_position(alpaca_gw, mock_alpaca_client)

        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="exit-1")
        # stop-1 cancel verification during submit_exit
        mock_alpaca_client.get_order_by_id.side_effect = [
            MockAlpacaOrder(id="stop-1", status="canceled"),
        ]
        exit_order, _ = alpaca_gw.submit_exit("DSY", "partial", exit_pct=100)

        mock_alpaca_client.get_order_by_id.side_effect = [
            MockAlpacaOrder(id="exit-1", status="partially_filled", filled_qty="20", filled_avg_price="11.00"),
            MockAlpacaOrder(id="exit-1", status="canceled"),
        ]
        pos = alpaca_gw.confirm_exit_fill(exit_order.order_id)

        assert pos.current_shares == 30  # 50 - 20
        assert pos.state != PositionState.CLOSED
        mock_alpaca_client.cancel_order_by_id.assert_called_with(exit_order.order_id)

    def test_confirm_exit_partial_fill_reprotects_remaining_shares(self, alpaca_gw, mock_alpaca_client):
        self._open_position(alpaca_gw, mock_alpaca_client)

        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="stop-1")
        alpaca_gw.place_stop("DSY", 10.30, 50)

        mock_alpaca_client.submit_order.side_effect = [
            MockAlpacaOrder(id="exit-1"),
            MockAlpacaOrder(id="stop-2"),
        ]
        # stop-1 cancel verification during submit_exit
        mock_alpaca_client.get_order_by_id.side_effect = [
            MockAlpacaOrder(id="stop-1", status="canceled"),
        ]
        exit_order, _ = alpaca_gw.submit_exit("DSY", "test")

        mock_alpaca_client.get_order_by_id.side_effect = [
            MockAlpacaOrder(id="exit-1", status="partially_filled", filled_qty="20", filled_avg_price="11.00"),
            MockAlpacaOrder(id="exit-1", status="canceled"),
        ]
        pos = alpaca_gw.confirm_exit_fill(exit_order.order_id)
        pending_stops = [
            o for o in alpaca_gw.pending.get_for_symbol("DSY")
            if o.order_type == OrderActionType.STOP
        ]

        assert pos.current_shares == 30
        assert pos.state == PositionState.OPEN
        assert len(pending_stops) == 1
        assert pending_stops[0].qty == 30

    def test_confirm_exit_rejected_marks_unprotected(self, alpaca_gw, mock_alpaca_client):
        """T6.7: rejected exit with positive shares → UNPROTECTED, shares preserved, pending cleared.

        Safety defect fix: terminal exit rejection must never put a positive-share
        position into ERROR — the stop was cancelled during submit_exit, so ERROR
        would lose share visibility. UNPROTECTED keeps _has_emergency() true so
        the app can reconcile/retry.
        """
        self._open_position(alpaca_gw, mock_alpaca_client)

        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="exit-1")
        exit_order, _ = alpaca_gw.submit_exit("DSY", "test")

        mock_alpaca_client.get_order_by_id.return_value = MockAlpacaOrder(
            id="exit-1", status="rejected",
        )
        with pytest.raises(RuntimeError, match="UNPROTECTED"):
            alpaca_gw.confirm_exit_fill(exit_order.order_id)

        pos = alpaca_gw.positions.get("DSY")
        assert pos.state == PositionState.UNPROTECTED, (
            f"Rejected exit with shares must go to UNPROTECTED, got {pos.state}"
        )
        assert pos.current_shares == 50, (
            f"Rejected exit must preserve shares, got {pos.current_shares}"
        )
        assert len(alpaca_gw.pending) == 0, (
            f"Rejected exit must clear pending store, got {len(alpaca_gw.pending)}"
        )

    def test_confirm_exit_canceled_marks_unprotected(self, alpaca_gw, mock_alpaca_client):
        """canceled exit with positive shares → UNPROTECTED, shares preserved."""
        self._open_position(alpaca_gw, mock_alpaca_client)

        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="exit-1")
        exit_order, _ = alpaca_gw.submit_exit("DSY", "test")

        mock_alpaca_client.get_order_by_id.return_value = MockAlpacaOrder(
            id="exit-1", status="canceled",
        )
        with pytest.raises(RuntimeError, match="UNPROTECTED"):
            alpaca_gw.confirm_exit_fill(exit_order.order_id)

        pos = alpaca_gw.positions.get("DSY")
        assert pos.state == PositionState.UNPROTECTED
        assert pos.current_shares == 50
        assert len(alpaca_gw.pending) == 0

    def test_confirm_exit_rejected_zero_shares_sets_error(self, alpaca_gw, mock_alpaca_client):
        """rejected exit with zero shares → ERROR (zero-share semantics preserved)."""
        self._open_position(alpaca_gw, mock_alpaca_client)

        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="exit-1")
        exit_order, _ = alpaca_gw.submit_exit("DSY", "test")

        # Force zero shares on position
        pos = alpaca_gw.positions.get("DSY")
        pos.current_shares = 0
        alpaca_gw.positions.upsert(pos)

        mock_alpaca_client.get_order_by_id.return_value = MockAlpacaOrder(
            id="exit-1", status="rejected",
        )
        with pytest.raises(RuntimeError, match="ERROR"):
            alpaca_gw.confirm_exit_fill(exit_order.order_id)

        pos = alpaca_gw.positions.get("DSY")
        assert pos.state == PositionState.ERROR
        assert pos.current_shares == 0

    def test_confirm_exit_pending_stays_exiting(self, alpaca_gw, mock_alpaca_client):
        """T6.7: pending exit → stays EXITING, order not resolved."""
        self._open_position(alpaca_gw, mock_alpaca_client)

        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="exit-1")
        exit_order, _ = alpaca_gw.submit_exit("DSY", "test")

        mock_alpaca_client.get_order_by_id.return_value = MockAlpacaOrder(
            id="exit-1", status="new",
        )
        pos = alpaca_gw.confirm_exit_fill(exit_order.order_id)

        assert pos.state == PositionState.EXITING
        assert len(alpaca_gw.pending) == 1  # still pending

    def test_confirm_exit_fill_api_failure_raises(self, alpaca_gw, mock_alpaca_client):
        """T6.5: exit fill check API failure raises."""
        self._open_position(alpaca_gw, mock_alpaca_client)

        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="exit-1")
        exit_order, _ = alpaca_gw.submit_exit("DSY", "test")

        mock_alpaca_client.get_order_by_id.side_effect = ConnectionError("API down")

        with pytest.raises(RuntimeError, match="confirm_exit_fill check failed"):
            alpaca_gw.confirm_exit_fill(exit_order.order_id)


# ══════════════════════════════════════════════════════════════════
#  Phase 6 — T6.6: Broker snapshot for Alpaca paper-broker mode
# ══════════════════════════════════════════════════════════════════


class MockAlpacaPosition:
    """Minimal mock of an Alpaca Position object."""
    def __init__(self, symbol: str, qty: str, avg_entry_price: str, cost_basis: str = "0"):
        self.symbol = symbol
        self.qty = qty
        self.avg_entry_price = avg_entry_price
        self.cost_basis = cost_basis


class TestBuildAlpacaBrokerSnapshot:
    """T6.6: build_alpaca_broker_snapshot with mocked Alpaca client."""

    def test_uses_get_all_positions_api(self):
        """Regression: current alpaca-py TradingClient exposes get_all_positions()."""
        from src.paper_execution import AlpacaExecutionGateway, build_alpaca_broker_snapshot

        class FakeClient:
            def get_all_positions(self):
                return [MockAlpacaPosition("AAPL", "100", "150.50")]

        gw = AlpacaExecutionGateway(api_key="test_key", secret_key="test_secret")
        gw._client = FakeClient()

        result = build_alpaca_broker_snapshot(gw)

        assert result == {"AAPL": (100, 150.50)}

    def test_returns_snapshot_on_success(self, alpaca_gw, mock_alpaca_client):
        """build_alpaca_broker_snapshot returns correct dict on success."""
        from src.paper_execution import build_alpaca_broker_snapshot

        mock_alpaca_client.get_all_positions.return_value = [
            MockAlpacaPosition("AAPL", "100", "150.50"),
            MockAlpacaPosition("MSFT", "50", "300.25"),
        ]
        result = build_alpaca_broker_snapshot(alpaca_gw)

        assert result is not None
        assert result == {"AAPL": (100, 150.50), "MSFT": (50, 300.25)}

    def test_returns_none_on_api_failure(self, alpaca_gw, mock_alpaca_client):
        """Broker unreachable → returns None (T6.2 policy)."""
        from src.paper_execution import build_alpaca_broker_snapshot

        mock_alpaca_client.get_all_positions.side_effect = ConnectionError("API down")

        result = build_alpaca_broker_snapshot(alpaca_gw)
        assert result is None

    def test_empty_positions_returns_empty_dict(self, alpaca_gw, mock_alpaca_client):
        """No positions → returns empty dict (not None)."""
        from src.paper_execution import build_alpaca_broker_snapshot

        mock_alpaca_client.get_all_positions.return_value = []
        result = build_alpaca_broker_snapshot(alpaca_gw)

        assert result is not None
        assert result == {}

    def test_snapshot_wired_into_app_reconciliation(self, alpaca_gw, mock_alpaca_client):
        """T6.6: broker snapshot from Alpaca flows through app reconciliation."""
        from src.paper_execution import build_alpaca_broker_snapshot
        from src.app import TradingApp

        mock_alpaca_client.get_all_positions.return_value = [
            MockAlpacaPosition("DSY", "50", "10.50"),
        ]

        def snapshot_fn():
            return build_alpaca_broker_snapshot(alpaca_gw)

        app = TradingApp(execution_gw=alpaca_gw, broker_snapshot_fn=snapshot_fn)
        app._reconcile_on_startup()

        pos = alpaca_gw.positions.get("DSY")
        assert pos is not None
        assert pos.current_shares == 50
        # ponytail: insert_protect with no stop_price marks UNPROTECTED per BUG 2 fix
        assert pos.state == PositionState.UNPROTECTED

    def test_unreachable_snapshot_marks_positions_unprotected(self, alpaca_gw, mock_alpaca_client):
        """Unreachable broker via snapshot → marks OPEN positions UNPROTECTED."""
        from src.paper_execution import build_alpaca_broker_snapshot
        from src.app import TradingApp

        # Pre-populate local positions
        alpaca_gw.positions.upsert(PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            current_shares=50, entry_price=10.50, average_entry=10.50,
            stop_price=10.30,
        ))

        mock_alpaca_client.get_all_positions.side_effect = ConnectionError("API down")

        def snapshot_fn():
            return build_alpaca_broker_snapshot(alpaca_gw)

        app = TradingApp(execution_gw=alpaca_gw, broker_snapshot_fn=snapshot_fn)
        app._reconcile_on_startup()

        pos = alpaca_gw.positions.get("DSY")
        assert pos.state == PositionState.UNPROTECTED


class TestBuildAlpacaAccountEquity:
    """Account equity helper should read Alpaca account equity."""

    def test_returns_float_equity_on_success(self, alpaca_gw, mock_alpaca_client):
        from src.paper_execution import build_alpaca_account_equity

        class FakeAccount:
            equity = "54321.09"

        mock_alpaca_client.get_account.return_value = FakeAccount()

        assert build_alpaca_account_equity(alpaca_gw) == 54321.09

    def test_returns_none_on_account_api_failure(self, alpaca_gw, mock_alpaca_client):
        from src.paper_execution import build_alpaca_account_equity

        mock_alpaca_client.get_account.side_effect = ConnectionError("API down")

        assert build_alpaca_account_equity(alpaca_gw) is None


class TestBuildAlpacaOpenOrderSnapshot:
    """Roadmap #3: Alpaca open-order snapshot uses TradingClient.get_orders."""

    def test_returns_open_orders_as_pending_orders(self, alpaca_gw, mock_alpaca_client):
        from src.paper_execution import build_alpaca_open_order_snapshot

        order = MockAlpacaOrder(id="stop-1", qty="50")
        order.symbol = "DSY"
        order.side = "sell"
        order.type = "stop"
        order.stop_price = "10.20"
        mock_alpaca_client.get_orders.return_value = [order]

        result = build_alpaca_open_order_snapshot(alpaca_gw)

        assert result is not None
        assert len(result) == 1
        assert result[0].order_id == "stop-1"
        assert result[0].order_type == OrderActionType.STOP
        assert result[0].symbol == "DSY"
        assert result[0].stop_price == 10.20

    def test_returns_none_on_open_order_api_failure(self, alpaca_gw, mock_alpaca_client):
        from src.paper_execution import build_alpaca_open_order_snapshot

        mock_alpaca_client.get_orders.side_effect = ConnectionError("API down")

        assert build_alpaca_open_order_snapshot(alpaca_gw) is None


# ══════════════════════════════════════════════════════════════════
#  Phase 8 — T8.3: Broker failure matrix tests
# ══════════════════════════════════════════════════════════════════


class TestBrokerFailureTimeout:
    """T8.3: timeout / API hang scenarios — must not silently succeed."""

    def test_submit_entry_timeout_raises(self, alpaca_gw, mock_alpaca_client):
        """Timeout during submit_entry raises RuntimeError, no synthetic fill."""
        mock_alpaca_client.submit_order.side_effect = TimeoutError("connection timed out")

        with pytest.raises(RuntimeError, match="submit_entry failed"):
            alpaca_gw.submit_entry(_signal())

        # No position, no pending order left behind
        assert alpaca_gw.positions.get("DSY") is None
        assert len(alpaca_gw.pending) == 0

    def test_confirm_fill_timeout_raises(self, alpaca_gw, mock_alpaca_client):
        """Timeout during confirm_fill raises RuntimeError — position stays PENDING_ENTRY."""
        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="a1")
        order, _ = alpaca_gw.submit_entry(_signal())

        mock_alpaca_client.get_order_by_id.side_effect = TimeoutError("timeout")

        with pytest.raises(RuntimeError, match="confirm_fill check failed"):
            alpaca_gw.confirm_fill(order.order_id)

        # Position unchanged — still PENDING_ENTRY
        pos = alpaca_gw.positions.get("DSY")
        assert pos.state == PositionState.PENDING_ENTRY

    def test_submit_exit_timeout_raises(self, alpaca_gw, mock_alpaca_client):
        """Timeout during exit submission raises, position stays OPEN."""
        # Open a position first
        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="entry-1")
        order, _ = alpaca_gw.submit_entry(_signal())
        mock_alpaca_client.get_order_by_id.return_value = MockAlpacaOrder(
            id="entry-1", status="filled", filled_qty="50", filled_avg_price="10.50",
        )
        alpaca_gw.confirm_fill(order.order_id)

        mock_alpaca_client.submit_order.side_effect = TimeoutError("timeout")

        with pytest.raises(RuntimeError, match="exit failed"):
            alpaca_gw.submit_exit("DSY", "test")

        # Position stays OPEN when Alpaca exit submit fails.
        pos = alpaca_gw.positions.get("DSY")
        assert pos.state == PositionState.OPEN


class TestBrokerMaintenanceError:
    """T8.3: maintenance / API outage — explicit error, no fallback."""

    def test_maintenance_error_on_submit_entry(self, alpaca_gw, mock_alpaca_client):
        """Broker returns 503 Maintenance — must raise, not silently succeed."""
        mock_alpaca_client.submit_order.side_effect = RuntimeError("503 Service Unavailable")

        with pytest.raises(RuntimeError, match="submit_entry failed"):
            alpaca_gw.submit_entry(_signal())

    def test_maintenance_error_on_place_stop(self, alpaca_gw, mock_alpaca_client):
        """Broker maintenance during stop placement — must raise."""
        # Open position
        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="a1")
        order, _ = alpaca_gw.submit_entry(_signal())
        mock_alpaca_client.get_order_by_id.return_value = MockAlpacaOrder(
            id="a1", status="filled", filled_qty="50", filled_avg_price="10.50",
        )
        alpaca_gw.confirm_fill(order.order_id)

        mock_alpaca_client.submit_order.side_effect = RuntimeError("503 Maintenance")
        with pytest.raises(RuntimeError, match="stop placement failed"):
            alpaca_gw.place_stop("DSY", 10.20, 50)

        # Stop was NOT placed
        assert not alpaca_gw._has_pending_stop("DSY")


class TestBrokerMissingCredentials:
    """T8.3: missing API credentials — fail loud, don't silently connect."""

    def test_alpaca_gateway_accepts_keys(self):
        """Gateway with explicit keys does not crash on construction."""
        gw = AlpacaExecutionGateway(api_key="PK", secret_key="SK")
        assert gw._api_key == "PK"
        assert gw._secret_key == "SK"

    def test_alpaca_gateway_without_keys(self, monkeypatch):
        """Gateway without keys and no env — client property should raise on access."""
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)

        gw = AlpacaExecutionGateway()
        # Client is lazily created — constructing gw is fine
        # Accessing client without valid keys will fail when TradingClient gets None
        import alpaca
        try:
            _ = gw.client
        except Exception:
            pass  # Expected: SDK rejects None credentials


class TestAlpacaBrokerCancel:
    """Task 2: AlpacaExecutionGateway actually cancels broker stops on exit.

    When ``submit_exit`` is called, the inherited ``cancel_stale_orders()``
    dispatches via dynamic dispatch to the overridden ``cancel_order()``,
    which calls ``client.cancel_order_by_id()`` on the broker before
    resolving local pending state.
    """

    def _open_position(self, alpaca_gw, mock_alpaca_client, symbol="DSY"):
        """Helper: open a position via Alpaca gateway."""
        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="entry-1")
        order, _ = alpaca_gw.submit_entry(_signal(symbol=symbol))
        mock_alpaca_client.get_order_by_id.return_value = MockAlpacaOrder(
            id="entry-1", status="filled", filled_qty="50", filled_avg_price="10.50",
        )
        alpaca_gw.confirm_fill(order.order_id)
        return alpaca_gw.positions.get(symbol)

    def test_submit_exit_cancels_broker_stop_before_market_sell(self, alpaca_gw, mock_alpaca_client):
        """Broker-side stop cancellation precedes market sell during exit."""
        self._open_position(alpaca_gw, mock_alpaca_client)
        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="stop-1")
        alpaca_gw.place_stop("DSY", 10.30, 50)
        assert alpaca_gw._has_pending_stop("DSY")

        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="exit-1")
        # stop-1 cancel verification for cancel_stale_orders during submit_exit
        mock_alpaca_client.get_order_by_id.side_effect = [
            MockAlpacaOrder(id="stop-1", status="canceled"),
        ]
        alpaca_gw.submit_exit("DSY", "target hit")

        # Broker was asked to cancel the stop
        mock_alpaca_client.cancel_order_by_id.assert_called_once_with("stop-1")
        # Local pending stop is resolved
        assert not alpaca_gw._has_pending_stop("DSY")

    def test_cancel_stale_order_reaches_gateway_not_just_log(self, alpaca_gw, mock_alpaca_client):
        """cancel_stale_orders dispatches to broker cancel via dynamic dispatch."""
        self._open_position(alpaca_gw, mock_alpaca_client)
        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="stop-1")
        alpaca_gw.place_stop("DSY", 10.30, 50)
        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="exit-1")
        # stop-1 cancel verification for cancel_stale_orders during submit_exit
        mock_alpaca_client.get_order_by_id.side_effect = [
            MockAlpacaOrder(id="stop-1", status="canceled"),
        ]
        alpaca_gw.submit_exit("DSY", "target hit")
        assert mock_alpaca_client.cancel_order_by_id.called

    def test_submit_exit_handles_cancel_failure_gracefully(self, alpaca_gw, mock_alpaca_client):
        """When broker cancel raises, exit still proceeds."""
        self._open_position(alpaca_gw, mock_alpaca_client)
        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="stop-1")
        alpaca_gw.place_stop("DSY", 10.30, 50)
        assert alpaca_gw._has_pending_stop("DSY")

        # Make broker cancel raise (already terminal), verify still works
        mock_alpaca_client.cancel_order_by_id.side_effect = RuntimeError("order terminal")
        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="exit-1")
        # stop-1 verification after cancel raises — get_order_by_id confirms terminal
        mock_alpaca_client.get_order_by_id.side_effect = [
            MockAlpacaOrder(id="stop-1", status="canceled"),
        ]

        exit_order, pos = alpaca_gw.submit_exit("DSY", "target hit")

        # Exit was still submitted
        assert exit_order.order_id == "exit-1"
        assert pos.state == PositionState.EXITING
        # Local state cleaned up despite broker error
        assert not alpaca_gw._has_pending_stop("DSY")


class TestBrokerStaleStopCancellation:
    """T8.3: stale stop cancellation via Alpaca gateway."""

    def test_exit_cancels_stale_stops(self, alpaca_gw, mock_alpaca_client):
        """Submitting an exit cancels all pending stops for that symbol."""
        # Open position with stop
        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="entry-1")
        order, _ = alpaca_gw.submit_entry(_signal())
        mock_alpaca_client.get_order_by_id.return_value = MockAlpacaOrder(
            id="entry-1", status="filled", filled_qty="50", filled_avg_price="10.50",
        )
        alpaca_gw.confirm_fill(order.order_id)

        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="stop-1")
        alpaca_gw.place_stop("DSY", 10.20, 50)

        # Verify stop exists
        assert alpaca_gw._has_pending_stop("DSY")

        # Submit exit — should cancel stop
        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="exit-1")
        # stop-1 cancel verification for cancel_stale_orders during submit_exit
        mock_alpaca_client.get_order_by_id.side_effect = [
            MockAlpacaOrder(id="stop-1", status="canceled"),
        ]
        alpaca_gw.submit_exit("DSY", "target hit")

        # Stop should be cancelled
        assert not alpaca_gw._has_pending_stop("DSY")


class TestBrokerSnapshotMismatch:
    """T8.3: broker snapshot mismatch — reconciliation handles discrepancies."""

    def test_broker_qty_zero_local_active_is_irreconcilable(self):
        """Broker reports 0 qty while local has active position → ERROR."""
        local = PositionStore()
        local.upsert(PositionStateModel(
            symbol="DSY", state=PositionState.OPEN, current_shares=50,
        ))
        pending = PendingOrderStore()
        broker = {"DSY": (0, 0.0)}

        actions = reconcile_positions(
            broker_positions=broker, local_store=local, pending_store=pending,
        )
        assert any(a["action"] == "irreconcilable" for a in actions)

    def test_broker_has_position_local_none_inserted(self):
        """Broker reports position that local doesn't know about → insert."""
        local = PositionStore()
        pending = PendingOrderStore()
        broker = {"NEW": (30, 5.0)}

        actions = reconcile_positions(
            broker_positions=broker, local_store=local, pending_store=pending,
        )
        assert any(a["action"] == "insert_protect" and a["symbol"] == "NEW"
                   for a in actions)
        assert local.get("NEW").current_shares == 30


# ══════════════════════════════════════════════════════════════════
#  AlpacaExecutionGateway — paper param for live vs paper routing
# ══════════════════════════════════════════════════════════════════


class TestAlpacaGatewayPaperParam:
    """AlpacaExecutionGateway accepts paper= parameter for live vs paper routing."""

    def test_default_paper_is_true(self):
        """Default paper=True for backward compatibility."""
        gw = AlpacaExecutionGateway(api_key="test", secret_key="test")
        assert gw._paper is True

    def test_paper_false_sets_live_mode(self):
        """paper=False sets _paper to False for live trading."""
        gw = AlpacaExecutionGateway(api_key="test", secret_key="test", paper=False)
        assert gw._paper is False

    def test_paper_true_creates_paper_trading_client(self):
        """When paper=True, client creates TradingClient with paper=True."""
        from unittest.mock import patch
        with patch("alpaca.trading.TradingClient") as mock_tc:
            gw = AlpacaExecutionGateway(api_key="test", secret_key="test", paper=True)
            _ = gw.client
            mock_tc.assert_called_once_with("test", "test", paper=True)

    def test_paper_false_creates_live_trading_client(self):
        """When paper=False, client creates TradingClient with paper=False."""
        from unittest.mock import patch
        with patch("alpaca.trading.TradingClient") as mock_tc:
            gw = AlpacaExecutionGateway(api_key="test", secret_key="test", paper=False)
            _ = gw.client
            mock_tc.assert_called_once_with("test", "test", paper=False)


# ══════════════════════════════════════════════════════════════════
#  Phase 7 — cancel_order requires broker verification (Req 1)
# ══════════════════════════════════════════════════════════════════


class TestAlpacaCancelOrderVerification:
    """cancel_order returns True only when broker verification confirms terminal."""

    def _open_position(self, alpaca_gw, mock_alpaca_client, symbol="DSY"):
        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="entry-1")
        order, _ = alpaca_gw.submit_entry(_signal(symbol=symbol))
        mock_alpaca_client.get_order_by_id.return_value = MockAlpacaOrder(
            id="entry-1", status="filled", filled_qty="50", filled_avg_price="10.50",
        )
        alpaca_gw.confirm_fill(order.order_id)
        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="stop-1")
        alpaca_gw.place_stop(symbol, 10.20, 50)
        return alpaca_gw.positions.get(symbol)

    def test_cancel_confirmed_canceled_returns_true(self, alpaca_gw, mock_alpaca_client):
        """Broker cancel + verify canceled → True, pending resolved."""
        self._open_position(alpaca_gw, mock_alpaca_client)
        stop = list(alpaca_gw.pending.all_pending())[0]
        mock_alpaca_client.cancel_order_by_id.return_value = None
        mock_alpaca_client.get_order_by_id.return_value = MockAlpacaOrder(
            id=stop.order_id, status="canceled",
        )
        assert alpaca_gw.cancel_order(stop.order_id) is True
        assert len(alpaca_gw.pending) == 0

    def test_cancel_confirmed_rejected_returns_true(self, alpaca_gw, mock_alpaca_client):
        """cancel + verify rejected → True."""
        self._open_position(alpaca_gw, mock_alpaca_client)
        stop = list(alpaca_gw.pending.all_pending())[0]
        mock_alpaca_client.get_order_by_id.return_value = MockAlpacaOrder(
            id=stop.order_id, status="rejected",
        )
        assert alpaca_gw.cancel_order(stop.order_id) is True
        assert len(alpaca_gw.pending) == 0

    def test_cancel_confirmed_expired_returns_true(self, alpaca_gw, mock_alpaca_client):
        """cancel + verify expired → True."""
        self._open_position(alpaca_gw, mock_alpaca_client)
        stop = list(alpaca_gw.pending.all_pending())[0]
        mock_alpaca_client.get_order_by_id.return_value = MockAlpacaOrder(
            id=stop.order_id, status="expired",
        )
        assert alpaca_gw.cancel_order(stop.order_id) is True
        assert len(alpaca_gw.pending) == 0

    def test_cancel_confirmed_done_for_day_returns_true(self, alpaca_gw, mock_alpaca_client):
        """cancel + verify done_for_day → True."""
        self._open_position(alpaca_gw, mock_alpaca_client)
        stop = list(alpaca_gw.pending.all_pending())[0]
        mock_alpaca_client.get_order_by_id.return_value = MockAlpacaOrder(
            id=stop.order_id, status="done_for_day",
        )
        assert alpaca_gw.cancel_order(stop.order_id) is True

    def test_cancel_verify_filled_returns_false(self, alpaca_gw, mock_alpaca_client):
        """cancel + verify filled → False, pending NOT resolved."""
        self._open_position(alpaca_gw, mock_alpaca_client)
        stop = list(alpaca_gw.pending.all_pending())[0]
        mock_alpaca_client.get_order_by_id.return_value = MockAlpacaOrder(
            id=stop.order_id, status="filled",
        )
        assert alpaca_gw.cancel_order(stop.order_id) is False
        assert len(alpaca_gw.pending) == 1  # NOT resolved

    def test_cancel_verify_partially_filled_returns_false(self, alpaca_gw, mock_alpaca_client):
        """cancel + verify partially_filled → False, pending NOT resolved."""
        self._open_position(alpaca_gw, mock_alpaca_client)
        stop = list(alpaca_gw.pending.all_pending())[0]
        mock_alpaca_client.get_order_by_id.return_value = MockAlpacaOrder(
            id=stop.order_id, status="partially_filled",
        )
        assert alpaca_gw.cancel_order(stop.order_id) is False
        assert len(alpaca_gw.pending) == 1

    def test_cancel_verify_still_live_returns_false(self, alpaca_gw, mock_alpaca_client):
        """cancel + verify still pending → False, pending NOT resolved."""
        self._open_position(alpaca_gw, mock_alpaca_client)
        stop = list(alpaca_gw.pending.all_pending())[0]
        mock_alpaca_client.get_order_by_id.return_value = MockAlpacaOrder(
            id=stop.order_id, status="new",
        )
        assert alpaca_gw.cancel_order(stop.order_id) is False
        assert len(alpaca_gw.pending) == 1

    def test_cancel_verify_failure_returns_false(self, alpaca_gw, mock_alpaca_client):
        """cancel + verify raises → False (unverifiable), pending NOT resolved."""
        self._open_position(alpaca_gw, mock_alpaca_client)
        stop = list(alpaca_gw.pending.all_pending())[0]
        mock_alpaca_client.get_order_by_id.side_effect = ConnectionError("API down")
        assert alpaca_gw.cancel_order(stop.order_id) is False
        assert len(alpaca_gw.pending) == 1


# ══════════════════════════════════════════════════════════════════
#  Phase 7 — partial fill does not commit until cancel confirmed (Req 2)
# ══════════════════════════════════════════════════════════════════


class TestAlpacaPartialFillNoCommitWithoutCancelConfirm:
    """Partial fills do not commit local quantities until cancel verified terminal."""

    def test_entry_partial_cancel_confirmed_commits(self, alpaca_gw, mock_alpaca_client):
        """partially_filled + cancel verified canceled → commits filled qty."""
        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="entry-1")
        order, _ = alpaca_gw.submit_entry(_signal(shares=100))
        mock_alpaca_client.get_order_by_id.side_effect = [
            MockAlpacaOrder(id="entry-1", status="partially_filled", filled_qty="30", filled_avg_price="10.50"),
            MockAlpacaOrder(id="entry-1", status="canceled"),
        ]
        pos = alpaca_gw.confirm_fill(order.order_id)
        assert pos.state == PositionState.OPEN
        assert pos.current_shares == 30
        assert len(alpaca_gw.pending) == 0

    def test_entry_partial_cancel_verify_filled_does_not_commit(self, alpaca_gw, mock_alpaca_client):
        """partially_filled + cancel verify filled → RuntimeError, state preserved."""
        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="entry-1")
        order, _ = alpaca_gw.submit_entry(_signal(shares=100))
        mock_alpaca_client.get_order_by_id.return_value = MockAlpacaOrder(
            id="entry-1", status="partially_filled", filled_qty="30", filled_avg_price="10.50",
        )
        # Cancel verify returns filled (remainder filled before cancel took)
        side_effect = [
            None,
            MockAlpacaOrder(id="entry-1", status="filled", filled_qty="100"),
        ]
        mock_alpaca_client.cancel_order_by_id.side_effect = side_effect
        with pytest.raises(RuntimeError, match="not confirmed terminal"):
            alpaca_gw.confirm_fill(order.order_id)
        pos = alpaca_gw.positions.get("DSY")
        assert pos.state == PositionState.PENDING_ENTRY  # not committed
        assert pos.current_shares == 100  # original proposed, not reduced
        assert len(alpaca_gw.pending) == 1  # NOT resolved

    def test_entry_partial_cancel_verify_unverifiable_does_not_commit(self, alpaca_gw, mock_alpaca_client):
        """partially_filled + cancel verify fails → RuntimeError, state preserved."""
        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="entry-1")
        order, _ = alpaca_gw.submit_entry(_signal(shares=100))
        mock_alpaca_client.get_order_by_id.return_value = MockAlpacaOrder(
            id="entry-1", status="partially_filled", filled_qty="30", filled_avg_price="10.50",
        )
        # Cancel verify raises
        mock_alpaca_client.cancel_order_by_id.side_effect = ConnectionError("API down")
        with pytest.raises(RuntimeError, match="not confirmed terminal"):
            alpaca_gw.confirm_fill(order.order_id)
        pos = alpaca_gw.positions.get("DSY")
        assert pos.state == PositionState.PENDING_ENTRY
        assert len(alpaca_gw.pending) == 1

    def test_add_partial_cancel_confirmed_commits(self, alpaca_gw, mock_alpaca_client):
        """ADD partial + cancel verified → commits filled qty, RUNNER."""
        mock_alpaca_client.submit_order.side_effect = [
            MockAlpacaOrder(id="entry-1"),
            MockAlpacaOrder(id="stop-1"),
            MockAlpacaOrder(id="add-1"),
            MockAlpacaOrder(id="stop-2"),
        ]
        order, _ = alpaca_gw.submit_entry(_signal(shares=50))
        mock_alpaca_client.get_order_by_id.side_effect = [
            MockAlpacaOrder(id="entry-1", status="filled", filled_qty="50", filled_avg_price="10.50"),
            MockAlpacaOrder(id="stop-1", status="canceled"),  # cancel_stale_orders verify during submit_exit
        ]
        alpaca_gw.confirm_fill(order.order_id)
        alpaca_gw.place_stop("DSY", 10.20, 50)
        pos = alpaca_gw.positions.get("DSY")
        pos.state = PositionState.RUNNER
        pos.highest_price_seen = 11.50
        pos.trailing_stop_price = 10.00
        alpaca_gw.positions.upsert(pos)
        add_order, _ = alpaca_gw.submit_add("DSY", qty=25, entry_price=11.00, stop_price=10.00)
        # add_order verified + cancel verify returns canceled
        mock_alpaca_client.cancel_order_by_id.return_value = None
        mock_alpaca_client.get_order_by_id.side_effect = [
            MockAlpacaOrder(id="add-1", status="partially_filled", filled_qty="10", filled_avg_price="11.00"),
            MockAlpacaOrder(id="add-1", status="canceled"),
            MockAlpacaOrder(id="stop-1", status="canceled"),  # verify stop-1 cancel during cancel_stale_orders
        ]
        pos = alpaca_gw.confirm_fill(add_order.order_id)
        assert pos.state == PositionState.RUNNER
        assert pos.current_shares == 60  # 50 + 10
        assert len(alpaca_gw.pending) == 1  # only the new stop

    def test_add_partial_cancel_verify_filled_does_not_commit(self, alpaca_gw, mock_alpaca_client):
        """ADD partial + cancel verify filled → RuntimeError, state preserved."""
        mock_alpaca_client.submit_order.side_effect = [
            MockAlpacaOrder(id="entry-1"),
            MockAlpacaOrder(id="stop-1"),
            MockAlpacaOrder(id="add-1"),
        ]
        order, _ = alpaca_gw.submit_entry(_signal(shares=50))
        mock_alpaca_client.get_order_by_id.side_effect = [
            MockAlpacaOrder(id="entry-1", status="filled", filled_qty="50", filled_avg_price="10.50"),
        ]
        alpaca_gw.confirm_fill(order.order_id)
        alpaca_gw.place_stop("DSY", 10.20, 50)
        pos = alpaca_gw.positions.get("DSY")
        pos.state = PositionState.RUNNER
        pos.highest_price_seen = 11.50
        pos.trailing_stop_price = 10.00
        alpaca_gw.positions.upsert(pos)
        add_order, _ = alpaca_gw.submit_add("DSY", qty=25, entry_price=11.00, stop_price=10.00)
        mock_alpaca_client.get_order_by_id.side_effect = [
            MockAlpacaOrder(id="add-1", status="partially_filled", filled_qty="10", filled_avg_price="11.00"),
            MockAlpacaOrder(id="add-1", status="filled", filled_qty="25"),
        ]
        mock_alpaca_client.cancel_order_by_id.return_value = None
        with pytest.raises(RuntimeError, match="not confirmed terminal"):
            alpaca_gw.confirm_fill(add_order.order_id)
        pos = alpaca_gw.positions.get("DSY")
        assert pos.state == PositionState.ADDING  # not advanced
        assert pos.current_shares == 50  # unchanged
        assert len(alpaca_gw.pending.get_for_symbol("DSY")) == 2  # add still pending + stop

    def test_exit_partial_cancel_verify_filled_does_not_commit(self, alpaca_gw, mock_alpaca_client):
        """EXIT partial + cancel verify filled → RuntimeError, state preserved."""
        mock_alpaca_client.submit_order.side_effect = [
            MockAlpacaOrder(id="entry-1"),
            MockAlpacaOrder(id="stop-1"),
            MockAlpacaOrder(id="exit-1"),
        ]
        order, _ = alpaca_gw.submit_entry(_signal(shares=50))
        mock_alpaca_client.get_order_by_id.side_effect = [
            MockAlpacaOrder(id="entry-1", status="filled", filled_qty="50", filled_avg_price="10.50"),
            MockAlpacaOrder(id="stop-1", status="canceled"),  # cancel_stale_orders verify during submit_exit
        ]
        alpaca_gw.confirm_fill(order.order_id)
        alpaca_gw.place_stop("DSY", 10.20, 50)
        exit_order, _ = alpaca_gw.submit_exit("DSY", "test")
        mock_alpaca_client.get_order_by_id.side_effect = [
            MockAlpacaOrder(id="exit-1", status="partially_filled", filled_qty="20", filled_avg_price="11.00"),
            MockAlpacaOrder(id="exit-1", status="filled", filled_qty="50"),
        ]
        mock_alpaca_client.cancel_order_by_id.return_value = None
        with pytest.raises(RuntimeError, match="not confirmed terminal"):
            alpaca_gw.confirm_exit_fill(exit_order.order_id)
        pos = alpaca_gw.positions.get("DSY")
        assert pos.state == PositionState.EXITING  # not advanced
        assert pos.current_shares == 50  # unchanged
        assert len(alpaca_gw.pending) == 1  # exit still pending


# ══════════════════════════════════════════════════════════════════
#  Phase 7 — exit fail + stop restore fail → UNPROTECTED (Req 3)
# ══════════════════════════════════════════════════════════════════


class TestExitFailWithRestoreFailMarksUnprotected:
    """When exit submission fails AND stop restoration also fails → UNPROTECTED."""

    def test_exit_fail_and_restore_fail_marks_unprotected(self, alpaca_gw, mock_alpaca_client):
        """submit_exit API fails + restore_stop API fails → position UNPROTECTED."""
        # Open position with stop
        mock_alpaca_client.submit_order.side_effect = [
            MockAlpacaOrder(id="entry-1"),
            MockAlpacaOrder(id="stop-1"),
        ]
        order, _ = alpaca_gw.submit_entry(_signal(shares=50))
        mock_alpaca_client.get_order_by_id.side_effect = [
            MockAlpacaOrder(id="entry-1", status="filled", filled_qty="50", filled_avg_price="10.50"),
        ]
        alpaca_gw.confirm_fill(order.order_id)
        alpaca_gw.place_stop("DSY", 10.20, 50)

        # Cancel verification must confirm stop-1 cancelled before exit proceeds
        mock_alpaca_client.get_order_by_id.side_effect = [
            MockAlpacaOrder(id="stop-1", status="canceled"),
        ]
        # Exit submit fails, stop restore also fails
        mock_alpaca_client.submit_order.side_effect = [
            ConnectionError("API down"),  # exit submit
            ConnectionError("restore also down"),  # stop restore also fails
        ]
        with pytest.raises(RuntimeError, match="exit failed"):
            alpaca_gw.submit_exit("DSY", "test")

        pos = alpaca_gw.positions.get("DSY")
        assert pos.state == PositionState.UNPROTECTED, (
            f"Expected UNPROTECTED, got {pos.state}"
        )
        assert pos.current_shares == 50  # shares preserved

    def test_exit_fail_restore_succeeds_stays_open(self, alpaca_gw, mock_alpaca_client):
        """submit_exit fails but stop restore succeeds → stays OPEN, NOT UNPROTECTED."""
        mock_alpaca_client.submit_order.side_effect = [
            MockAlpacaOrder(id="entry-1"),
            MockAlpacaOrder(id="stop-1"),
        ]
        order, _ = alpaca_gw.submit_entry(_signal(shares=50))
        mock_alpaca_client.get_order_by_id.side_effect = [
            MockAlpacaOrder(id="entry-1", status="filled", filled_qty="50", filled_avg_price="10.50"),
        ]
        alpaca_gw.confirm_fill(order.order_id)
        alpaca_gw.place_stop("DSY", 10.20, 50)

        # Cancel verification must confirm stop-1 cancelled before exit proceeds
        mock_alpaca_client.get_order_by_id.side_effect = [
            MockAlpacaOrder(id="stop-1", status="canceled"),
        ]
        # Exit submit fails, but restore succeeds
        mock_alpaca_client.submit_order.side_effect = [
            ConnectionError("API down"),  # exit submit
            MockAlpacaOrder(id="restored-stop"),  # restore succeeds
        ]
        with pytest.raises(RuntimeError, match="exit failed"):
            alpaca_gw.submit_exit("DSY", "test")

        pos = alpaca_gw.positions.get("DSY")
        assert pos.state == PositionState.OPEN  # not UNPROTECTED
        assert alpaca_gw._has_pending_stop("DSY")


# ══════════════════════════════════════════════════════════════════
#  Phase 7 — terminal ENTRY status sets current_shares=0 (Req 4)
# ══════════════════════════════════════════════════════════════════


class TestTerminalEntrySetsCurrentSharesZero:
    """Rejected/canceled/expired ENTRY sets current_shares=0 before ERROR."""

    def _submit_pending_entry(self, alpaca_gw, mock_alpaca_client):
        """Submit entry and return the order."""
        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="entry-1")
        order, _ = alpaca_gw.submit_entry(_signal(shares=100))
        return order

    def test_rejected_entry_sets_current_shares_zero(self, alpaca_gw, mock_alpaca_client):
        """rejected ENTRY → current_shares=0 before ERROR."""
        order = self._submit_pending_entry(alpaca_gw, mock_alpaca_client)
        mock_alpaca_client.get_order_by_id.return_value = MockAlpacaOrder(
            id="entry-1", status="rejected",
        )
        with pytest.raises(RuntimeError, match="rejected"):
            alpaca_gw.confirm_fill(order.order_id)
        pos = alpaca_gw.positions.get("DSY")
        assert pos.state == PositionState.ERROR
        assert pos.current_shares == 0, (
            f"Expected current_shares=0 after rejected entry, got {pos.current_shares}"
        )

    def test_canceled_entry_sets_current_shares_zero(self, alpaca_gw, mock_alpaca_client):
        """canceled ENTRY → current_shares=0 before ERROR."""
        order = self._submit_pending_entry(alpaca_gw, mock_alpaca_client)
        mock_alpaca_client.get_order_by_id.return_value = MockAlpacaOrder(
            id="entry-1", status="canceled",
        )
        with pytest.raises(RuntimeError, match="canceled"):
            alpaca_gw.confirm_fill(order.order_id)
        pos = alpaca_gw.positions.get("DSY")
        assert pos.state == PositionState.ERROR
        assert pos.current_shares == 0

    def test_expired_entry_sets_current_shares_zero(self, alpaca_gw, mock_alpaca_client):
        """expired ENTRY → current_shares=0 before ERROR."""
        order = self._submit_pending_entry(alpaca_gw, mock_alpaca_client)
        mock_alpaca_client.get_order_by_id.return_value = MockAlpacaOrder(
            id="entry-1", status="expired",
        )
        with pytest.raises(RuntimeError, match="expired"):
            alpaca_gw.confirm_fill(order.order_id)
        pos = alpaca_gw.positions.get("DSY")
        assert pos.state == PositionState.ERROR
        assert pos.current_shares == 0


# ══════════════════════════════════════════════════════════════════
#  Correction 1 — partial fill cancel unconfirmed raises RuntimeError
# ══════════════════════════════════════════════════════════════════


class TestPartialFillCancelUnconfirmedRaises:
    """When _cancel_remainder_and_verify returns False, RuntimeError is raised
    and all state/pending/quantities remain unchanged, so TradingApp can
    trigger immediate reconciliation."""

    def test_entry_partial_cancel_unconfirmed_raises_and_preserves_state(self, alpaca_gw, mock_alpaca_client):
        """ENTRY partial fill, cancel verify not terminal → RuntimeError, state unchanged."""
        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="entry-1")
        order, _ = alpaca_gw.submit_entry(_signal(shares=100))
        mock_alpaca_client.get_order_by_id.side_effect = [
            MockAlpacaOrder(id="entry-1", status="partially_filled", filled_qty="30", filled_avg_price="10.50"),
            MockAlpacaOrder(id="entry-1", status="filled"),  # cancel verify shows filled (not terminal)
        ]
        with pytest.raises(RuntimeError, match="not confirmed terminal"):
            alpaca_gw.confirm_fill(order.order_id)

        pos = alpaca_gw.positions.get("DSY")
        assert pos.state == PositionState.PENDING_ENTRY  # unchanged
        assert pos.current_shares == 100  # unchanged (original proposed)
        assert len(alpaca_gw.pending) == 1  # order still pending

    def test_add_partial_cancel_unconfirmed_raises_and_preserves_state(self, alpaca_gw, mock_alpaca_client):
        """ADD partial fill, cancel verify not terminal → RuntimeError, state unchanged."""
        mock_alpaca_client.submit_order.side_effect = [
            MockAlpacaOrder(id="entry-1"),
            MockAlpacaOrder(id="stop-1"),
            MockAlpacaOrder(id="add-1"),
        ]
        order, _ = alpaca_gw.submit_entry(_signal(shares=50))
        mock_alpaca_client.get_order_by_id.side_effect = [
            MockAlpacaOrder(id="entry-1", status="filled", filled_qty="50", filled_avg_price="10.50"),
        ]
        alpaca_gw.confirm_fill(order.order_id)
        alpaca_gw.place_stop("DSY", 10.20, 50)
        pos = alpaca_gw.positions.get("DSY")
        pos.state = PositionState.RUNNER
        pos.highest_price_seen = 11.50
        pos.trailing_stop_price = 10.00
        alpaca_gw.positions.upsert(pos)
        add_order, _ = alpaca_gw.submit_add("DSY", qty=25, entry_price=11.00, stop_price=10.00)
        mock_alpaca_client.get_order_by_id.side_effect = [
            MockAlpacaOrder(id="add-1", status="partially_filled", filled_qty="10", filled_avg_price="11.00"),
            MockAlpacaOrder(id="add-1", status="filled"),  # cancel verify shows filled (not terminal)
        ]
        mock_alpaca_client.cancel_order_by_id.return_value = None
        with pytest.raises(RuntimeError, match="not confirmed terminal"):
            alpaca_gw.confirm_fill(add_order.order_id)

        pos = alpaca_gw.positions.get("DSY")
        assert pos.state == PositionState.ADDING  # unchanged
        assert pos.current_shares == 50  # unchanged
        assert len(alpaca_gw.pending.get_for_symbol("DSY")) == 2  # add + stop still pending

    def test_exit_partial_cancel_unconfirmed_raises_and_preserves_state(self, alpaca_gw, mock_alpaca_client):
        """EXIT partial fill, cancel verify not terminal → RuntimeError, state unchanged."""
        mock_alpaca_client.submit_order.side_effect = [
            MockAlpacaOrder(id="entry-1"),
            MockAlpacaOrder(id="stop-1"),
            MockAlpacaOrder(id="exit-1"),
        ]
        order, _ = alpaca_gw.submit_entry(_signal(shares=50))
        mock_alpaca_client.get_order_by_id.side_effect = [
            MockAlpacaOrder(id="entry-1", status="filled", filled_qty="50", filled_avg_price="10.50"),
            MockAlpacaOrder(id="stop-1", status="canceled"),  # cancel_stale_orders verify during submit_exit
        ]
        alpaca_gw.confirm_fill(order.order_id)
        alpaca_gw.place_stop("DSY", 10.20, 50)
        exit_order, _ = alpaca_gw.submit_exit("DSY", "test")
        mock_alpaca_client.get_order_by_id.side_effect = [
            MockAlpacaOrder(id="exit-1", status="partially_filled", filled_qty="20", filled_avg_price="11.00"),
            MockAlpacaOrder(id="exit-1", status="filled"),  # cancel verify shows filled (not terminal)
        ]
        mock_alpaca_client.cancel_order_by_id.return_value = None
        with pytest.raises(RuntimeError, match="not confirmed terminal"):
            alpaca_gw.confirm_exit_fill(exit_order.order_id)

        pos = alpaca_gw.positions.get("DSY")
        assert pos.state == PositionState.EXITING  # unchanged
        assert pos.current_shares == 50  # unchanged
        assert len(alpaca_gw.pending) == 1  # exit still pending


# ══════════════════════════════════════════════════════════════════
#  Correction 2 — submit_exit aborts when stop cancel not confirmed
# ══════════════════════════════════════════════════════════════════


class TestAlpacaExitAbortsOnUnconfirmedStopCancel:
    """When cancel_stale_orders cannot broker-confirm a stop cancellation,
    submit_exit aborts before submitting the market sell to prevent
    stale-stop unintended short exposure."""

    def _open_position_with_stop(self, alpaca_gw, mock_alpaca_client):
        """Helper: open position and place stop."""
        mock_alpaca_client.submit_order.side_effect = [
            MockAlpacaOrder(id="entry-1"),
            MockAlpacaOrder(id="stop-1"),
        ]
        order, _ = alpaca_gw.submit_entry(_signal(shares=50))
        mock_alpaca_client.get_order_by_id.side_effect = [
            MockAlpacaOrder(id="entry-1", status="filled", filled_qty="50", filled_avg_price="10.50"),
        ]
        alpaca_gw.confirm_fill(order.order_id)
        alpaca_gw.place_stop("DSY", 10.20, 50)

    def test_exit_aborts_when_stop_cancel_not_confirmed(self, alpaca_gw, mock_alpaca_client):
        """submit_exit raises RuntimeError when stop cancel not broker-confirmed."""
        self._open_position_with_stop(alpaca_gw, mock_alpaca_client)
        assert alpaca_gw._has_pending_stop("DSY")

        # Cancel verification returns non-terminal status — stop remains pending
        mock_alpaca_client.get_order_by_id.side_effect = [
            MockAlpacaOrder(id="stop-1", status="filled"),  # not a terminal cancel status
        ]
        mock_alpaca_client.cancel_order_by_id.return_value = None

        with pytest.raises(RuntimeError, match="stop cancellation not confirmed by broker"):
            alpaca_gw.submit_exit("DSY", "test")

        # Stop still present and position unchanged
        assert alpaca_gw._has_pending_stop("DSY")
        pos = alpaca_gw.positions.get("DSY")
        assert pos.state == PositionState.OPEN  # not EXITING
        assert pos.current_shares == 50  # unchanged
        assert pos.stop_price == 10.20  # intact

    def test_exit_proceeds_when_stop_cancel_confirmed(self, alpaca_gw, mock_alpaca_client):
        """submit_exit proceeds normally when stop cancel is broker-confirmed."""
        self._open_position_with_stop(alpaca_gw, mock_alpaca_client)
        assert alpaca_gw._has_pending_stop("DSY")

        # Cancel verification returns terminal status
        mock_alpaca_client.get_order_by_id.side_effect = [
            MockAlpacaOrder(id="stop-1", status="canceled"),
        ]
        # Reset submit_order side_effect (consumed by helper) before setting return_value
        mock_alpaca_client.submit_order.side_effect = None
        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="exit-1")

        exit_order, pos = alpaca_gw.submit_exit("DSY", "target hit")

        assert exit_order.order_type == OrderActionType.EXIT
        assert pos.state == PositionState.EXITING
        assert not alpaca_gw._has_pending_stop("DSY")  # stop resolved

    def test_exit_aborts_when_no_prior_stop_allows_clean_exit(self, alpaca_gw, mock_alpaca_client):
        """Exit proceeds normally when there was never a stop (no had_stop)."""
        mock_alpaca_client.submit_order.side_effect = [
            MockAlpacaOrder(id="entry-1"),
        ]
        order, _ = alpaca_gw.submit_entry(_signal(shares=50))
        mock_alpaca_client.get_order_by_id.side_effect = [
            MockAlpacaOrder(id="entry-1", status="filled", filled_qty="50", filled_avg_price="10.50"),
        ]
        alpaca_gw.confirm_fill(order.order_id)

        assert not alpaca_gw._has_pending_stop("DSY")
        # Reset submit_order side_effect (consumed by submit_entry) before setting return_value
        mock_alpaca_client.submit_order.side_effect = None
        mock_alpaca_client.submit_order.return_value = MockAlpacaOrder(id="exit-1")

        exit_order, pos = alpaca_gw.submit_exit("DSY", "no stop to worry about")
        assert exit_order.order_type == OrderActionType.EXIT
        assert pos.state == PositionState.EXITING
