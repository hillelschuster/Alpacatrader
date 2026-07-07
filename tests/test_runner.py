"""Runner capture tests for SPEC §11.4."""

from src.entries import Bar
from src.models.schemas import MoveState, PositionState, PositionStateModel
from src.runner import compute_atr, compute_runner_stop, should_promote_to_runner


def _bars() -> list[Bar]:
    return [
        Bar(open=10.00, high=10.20, low=9.90, close=10.10, volume=1_000),
        Bar(open=10.10, high=10.35, low=10.00, close=10.25, volume=1_000),
        Bar(open=10.25, high=10.50, low=10.10, close=10.40, volume=1_000),
        Bar(open=10.40, high=10.65, low=10.20, close=10.55, volume=1_000),
        Bar(open=10.55, high=10.85, low=10.30, close=10.75, volume=1_000),
        Bar(open=10.75, high=11.10, low=10.45, close=10.95, volume=2_000),
    ]


def _pos() -> PositionStateModel:
    return PositionStateModel(
        symbol="DSY",
        state=PositionState.OPEN,
        entry_price=10.00,
        average_entry=10.00,
        stop_price=9.50,
        current_shares=100,
    )


def test_atr_computation_uses_wilder_smoothing():
    assert compute_atr(_bars(), period=5) == 0.48


def test_runner_stop_ratchets_and_keeps_minimum_distance():
    stop = compute_runner_stop(
        highest_price_seen=11.50,
        atr=0.05,
        multiplier=2.5,
        current_stop=10.75,
        original_risk=0.50,
    )
    assert stop == 11.00


def test_runner_stop_never_moves_down():
    stop = compute_runner_stop(
        highest_price_seen=11.10,
        atr=0.20,
        multiplier=2.5,
        current_stop=10.80,
        original_risk=0.50,
    )
    assert stop == 10.80


def test_position_promotes_to_runner_on_strength():
    assert should_promote_to_runner(
        _pos(),
        bars=_bars(),
        current_price=10.80,
        vwap=10.20,
        move_state=MoveState.ACTIVE,
    ) is True


def test_runner_not_promoted_prematurely():
    assert should_promote_to_runner(
        _pos(),
        bars=_bars(),
        current_price=10.40,
        vwap=10.20,
        move_state=MoveState.ACTIVE,
    ) is False
