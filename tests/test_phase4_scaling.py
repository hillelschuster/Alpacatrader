"""Phase 4 scaling-in tests.

Scaling is the runner-catch mechanism: RUNNER proves strength, pulls back,
adds with fresh configured add risk, resizes protection, and remains a RUNNER.
"""

from pathlib import Path

from src.entries import Bar
from src.models.schemas import (
    MoveState,
    OrderActionType,
    PendingOrder,
    PositionState,
    PositionStateModel,
)
from src.paper_execution import AlpacaExecutionGateway, PaperExecutionGateway
from src.runner import should_add_to_runner
from src.sizing import add_shares_for_position, add_sizing
from src.state_machine import PositionStore, transition_position


def _add_setup_bars() -> list[Bar]:
    """VWAP reclaim after a pullback; enough structure for an add."""
    return [
        Bar(open=10.80, high=11.20, low=10.70, close=11.00, volume=1_000),
        Bar(open=11.00, high=11.40, low=10.90, close=11.25, volume=1_000),
        Bar(open=11.25, high=11.55, low=11.05, close=11.40, volume=1_000),
        Bar(open=11.40, high=11.45, low=10.95, close=11.05, volume=800),
        Bar(open=11.05, high=11.20, low=10.90, close=10.98, volume=700),
        Bar(open=10.98, high=11.35, low=10.96, close=11.28, volume=1_100),
    ]


def _runner_position(**kw) -> PositionStateModel:
    data = dict(
        symbol="DSY",
        state=PositionState.RUNNER,
        entry_price=10.00,
        average_entry=10.00,
        stop_price=9.50,
        current_shares=100,
        highest_price_seen=11.50,
        trailing_stop_price=10.00,
    )
    data.update(kw)
    return PositionStateModel(**data)


def test_add_sizing_anti_martingale_from_add_risk_pct():
    """Add risk is based on configured add_risk_pct of equity, then 50%/25%."""
    add1 = add_sizing(
        equity=100_000,
        add_risk_pct=0.0025,
        add_count=0,
        risk_per_share_at_add=0.50,
        max_open_risk_pct=0.03,
        total_open_risk=500,
        add_size_multiplier=0.5,
    )
    assert add1 == (250, 125.0, 625.0)

    add2 = add_shares_for_position(
        equity=100_000,
        add_risk_pct=0.0025,
        add_count=1,
        risk_per_share_at_add=0.50,
        max_open_risk_pct=0.03,
        total_open_risk=625,
        add_size_multiplier=0.5,
    )
    assert add2 == (125, 62.5, 687.5)


def test_add_blocked_by_risk_cap():
    assert add_sizing(
        equity=100_000,
        add_risk_pct=0.0025,
        add_count=0,
        risk_per_share_at_add=0.50,
        max_open_risk_pct=0.005,
        total_open_risk=490,
        add_size_multiplier=0.5,
    ) == (0, 0.0, 490.0)


def test_add_trigger_at_2r_requires_runner_and_setup():
    pos = _runner_position()
    signal = should_add_to_runner(
        pos,
        bars=_add_setup_bars(),
        current_price=11.28,
        vwap=11.00,
        move_state=MoveState.ACTIVE,
        activation_r_multiple=2.0,
        max_adds=2,
    )
    assert signal is not None
    assert signal.symbol == "DSY"
    assert signal.entry_price == 11.29
    assert signal.stop_price < signal.entry_price


def test_max_adds_blocks_add_trigger():
    pos = _runner_position(add_count=2)
    assert should_add_to_runner(
        pos,
        bars=_add_setup_bars(),
        current_price=11.28,
        vwap=11.00,
        move_state=MoveState.ACTIVE,
        activation_r_multiple=2.0,
        max_adds=2,
    ) is None


def test_add_trigger_requires_active_move_state():
    pos = _runner_position()
    assert should_add_to_runner(
        pos,
        bars=_add_setup_bars(),
        current_price=11.28,
        vwap=11.00,
        move_state=None,
        activation_r_multiple=2.0,
        max_adds=2,
    ) is None


def test_runner_to_adding_to_runner_transition_is_valid():
    pos = _runner_position()
    transition_position(pos, PositionState.ADDING)
    transition_position(pos, PositionState.RUNNER)
    assert pos.state == PositionState.RUNNER


def test_confirm_fill_handles_adding_and_resizes_protection():
    gw = PaperExecutionGateway()
    pos = _runner_position()
    gw.positions.upsert(pos)
    old_stop = gw.place_stop("DSY", 9.50, 100)

    order, pos_adding = gw.submit_add(
        "DSY",
        qty=25,
        entry_price=11.00,
        stop_price=11.00,
    )
    assert order.order_type == OrderActionType.ADD
    assert pos_adding.state == PositionState.ADDING

    filled = gw.confirm_fill(order.order_id)

    assert filled.state == PositionState.RUNNER
    assert filled.current_shares == 125
    assert filled.add_count == 1
    assert filled.average_entry == 10.20
    assert filled.stop_price == 11.00
    pending_stops = [
        o for o in gw.pending.get_for_symbol("DSY")
        if o.order_type == OrderActionType.STOP
    ]
    assert len(pending_stops) == 1
    assert pending_stops[0].qty == 125
    assert pending_stops[0].stop_price == 11.00
    assert pending_stops[0].order_id != old_stop.order_id


def test_place_stop_accepts_runner_state():
    gw = PaperExecutionGateway()
    gw.positions.upsert(_runner_position())
    order = gw.place_stop("DSY", 10.00, 100)
    assert order.order_type == OrderActionType.STOP


def test_add_failure_returns_to_runner_without_share_mutation():
    class RejectingAddGateway(PaperExecutionGateway):
        def submit_add(self, symbol: str, qty: int, entry_price: float, stop_price: float):
            pos = self.positions.get(symbol)
            assert pos is not None
            transition_position(pos, PositionState.ADDING)
            self.positions.upsert(pos)
            raise RuntimeError("rejected")

    gw = RejectingAddGateway()
    pos = _runner_position()
    gw.positions.upsert(pos)
    try:
        gw.submit_add("DSY", 25, 11.00, 11.00)
    except RuntimeError:
        failed = gw.positions.get("DSY")
        assert failed is not None
        transition_position(failed, PositionState.RUNNER, force=True)
        gw.positions.upsert(failed)

    after = gw.positions.get("DSY")
    assert after is not None
    assert after.state == PositionState.RUNNER
    assert after.current_shares == 100
    assert after.add_count == 0
    assert after.average_entry == 10.00


def test_position_state_survives_restart_with_add_count_and_average_entry(tmp_path: Path):
    store = PositionStore()
    store.upsert(_runner_position(add_count=1, average_entry=10.20, current_shares=125))
    path = tmp_path / "positions.json"

    store.save_to_disk(path)
    restored = PositionStore.load_from_disk(path).get("DSY")

    assert restored is not None
    assert restored.state == PositionState.RUNNER
    assert restored.add_count == 1
    assert restored.average_entry == 10.20
    assert restored.current_shares == 125


def test_stop_never_below_original_entry_for_add_order():
    gw = PaperExecutionGateway()
    gw.positions.upsert(_runner_position(entry_price=10.00))
    gw.pending.add(PendingOrder(
        symbol="DSY",
        order_id="stop-old",
        order_type=OrderActionType.STOP,
        side="sell",
        qty=100,
        status="submitted",
        stop_price=9.50,
    ))

    order, _ = gw.submit_add("DSY", qty=20, entry_price=9.80, stop_price=10.00)
    filled = gw.confirm_fill(order.order_id)

    assert filled.stop_price == 10.00


def test_alpaca_add_partial_fill_cancels_remainder_and_resizes_stop():
    class FakeAlpacaOrder:
        id = "add-1"
        status = "partially_filled"
        filled_qty = "10"
        filled_avg_price = "11.00"

    class FakeClient:
        def __init__(self):
            self.cancelled: list[str] = []

        def get_order_by_id(self, order_id: str):
            assert order_id == "add-1"
            return FakeAlpacaOrder()

        def cancel_order_by_id(self, order_id: str):
            self.cancelled.append(order_id)

        def submit_order(self, req):
            order = FakeAlpacaOrder()
            order.id = "stop-new"
            order.status = "new"
            return order

    gw = AlpacaExecutionGateway(api_key="key", secret_key="secret")
    fake_client = FakeClient()
    gw._client = fake_client
    pos = _runner_position(state=PositionState.ADDING)
    gw.positions.upsert(pos)
    gw.pending.add(PendingOrder(
        symbol="DSY",
        order_id="stop-old",
        order_type=OrderActionType.STOP,
        side="sell",
        qty=100,
        status="submitted",
        stop_price=9.50,
    ))
    gw.pending.add(PendingOrder(
        symbol="DSY",
        order_id="add-1",
        order_type=OrderActionType.ADD,
        side="buy",
        qty=25,
        status="submitted",
        limit_price=11.00,
        stop_price=11.00,
    ))

    filled = gw.confirm_fill("add-1")

    assert fake_client.cancelled == ["add-1", "stop-old"]
    assert filled.state == PositionState.RUNNER
    assert filled.current_shares == 110
    assert filled.average_entry == 10.09
    assert filled.add_count == 1
    assert filled.stop_price == 11.00
    assert all(o.order_type != OrderActionType.ADD for o in gw.pending.all_pending())
    stops = [o for o in gw.pending.all_pending() if o.order_type == OrderActionType.STOP]
    assert len(stops) == 1
    assert stops[0].qty == 110
