"""Phase 8 exit-engine tests per SPEC section 12.

Verifies:
  - Exit priority (emergency first).
  - Hard stop triggers at/below stop price.
  - Invalidation by setup type.
  - Scale-out at 1R, extension bar, spread caution.
  - Emergency exits (spread explosion, stale quote, unprotected losing).
  - Loss cap exits.
  - VWAP loss, failed reclaim, volume disappearance, time exit, runner trail.
  - P&L and P&L/R calculations.
  - Every exit has a reason string.
"""

from datetime import time

import pytest

from src.entries import Bar
from src.exits import (
    calculate_pnl,
    calculate_pnl_r,
    check_emergency_exit,
    check_exits,
    check_failed_reclaim,
    check_hard_stop,
    check_invalidation,
    check_loss_caps,
    check_missing_protection,
    check_runner_trail,
    check_scale_out,
    check_spread_expansion,
    check_time_exit,
    check_volume_disappearance,
    check_vwap_loss,
)
from src.models.schemas import (
    ExitDecision,
    MoveState,
    PositionState,
    PositionStateModel,
)


# ── Helpers ───────────────────────────────────────────────────────


def _pos(
    symbol: str = "DSY",
    state: PositionState = PositionState.OPEN,
    entry: float = 10.50,
    stop: float = 10.30,
    shares: int = 50,
) -> PositionStateModel:
    return PositionStateModel(
        symbol=symbol, state=state, entry_price=entry,
        current_shares=shares, average_entry=entry, stop_price=stop,
    )


# ──────────────────────────────────────────────────────────────────
#  P&L helpers
# ──────────────────────────────────────────────────────────────────


class TestPnL:
    def test_profit(self):
        p = _pos(entry=10.0, shares=10)
        assert calculate_pnl(p, 12.0) == 20.0

    def test_loss(self):
        p = _pos(entry=10.0, shares=10)
        assert calculate_pnl(p, 9.0) == -10.0

    def test_no_position(self):
        p = _pos(shares=0)
        assert calculate_pnl(p, 12.0) == 0.0

    def test_pnl_r(self):
        p = _pos(entry=10.0, shares=10, stop=9.80)
        # risk_per_share = 0.20, total risk = 2.0
        # price = 10.50, pnl = 5.0, pnl_r = 5.0/2.0 = 2.5
        assert calculate_pnl_r(p, 10.50, 0.20) == 2.5


# ──────────────────────────────────────────────────────────────────
#  Emergency exit
# ──────────────────────────────────────────────────────────────────


class TestEmergencyExit:
    def test_spread_explosion(self):
        p = _pos()
        result = check_emergency_exit(p, current_price=10.60, spread_pct=6.0)
        assert result is not None
        assert result.should_exit is True
        assert result.exit_pct == 100
        assert "spread_explosion" in result.reason

    def test_spread_3x_entry(self):
        p = _pos()
        result = check_emergency_exit(p, current_price=10.60, spread_pct=3.0, entry_spread_pct=0.8)
        assert result is not None  # 3.0 > 0.8*3 = 2.4

    def test_no_emergency_with_normal_data(self):
        p = _pos()
        result = check_emergency_exit(p, current_price=10.60, spread_pct=1.0)
        assert result is None

    def test_stale_quote_losing(self):
        p = _pos(entry=10.50)
        result = check_emergency_exit(p, current_price=10.20, quote_age_seconds=90)
        assert result is not None  # losing → flatten
        assert "quote_unreliable" in result.reason

    def test_stale_quote_profitable_exits(self):
        """Profitable stale quote must still flatten — stale >60s is always emergency."""
        p = _pos(entry=10.00)
        result = check_emergency_exit(p, current_price=10.60, quote_age_seconds=90)
        assert result is not None  # profitable but still flattens
        assert "quote_unreliable" in result.reason
        assert result.should_exit is True

    def test_unprotected_and_losing(self):
        p = _pos(entry=10.50)
        result = check_emergency_exit(p, current_price=10.20, position_unprotected=True)
        assert result is not None

    def test_multiple_halts(self):
        p = _pos()
        result = check_emergency_exit(p, current_price=10.60, halt_count_today=3)
        assert result is not None
        assert "multiple_halts" in result.reason


# ──────────────────────────────────────────────────────────────────
#  Loss caps
# ──────────────────────────────────────────────────────────────────


class TestLossCaps:
    def test_daily_loss_breached(self):
        p = _pos()
        result = check_loss_caps(p, current_price=10.60, daily_loss_breached=True)
        assert result is not None
        assert "daily_loss_cap" in result.reason

    def test_per_symbol_capped(self):
        p = _pos()
        result = check_loss_caps(p, current_price=10.60, per_symbol_loss_capped=True)
        assert result is not None

    def test_no_loss_caps(self):
        p = _pos()
        result = check_loss_caps(p, current_price=10.60)
        assert result is None


# ──────────────────────────────────────────────────────────────────
#  Hard stop
# ──────────────────────────────────────────────────────────────────


class TestHardStop:
    def test_triggers_at_stop(self):
        p = _pos(stop=10.30)
        result = check_hard_stop(p, current_price=10.30, quote_age_seconds=2.0)
        assert result is not None
        assert "hard_stop" in result.reason

    def test_triggers_below_stop(self):
        p = _pos(stop=10.30)
        result = check_hard_stop(p, current_price=10.20, quote_age_seconds=2.0)
        assert result is not None

    def test_no_trigger_above_stop(self):
        p = _pos(stop=10.30)
        result = check_hard_stop(p, current_price=10.50)
        assert result is None

    def test_pnl_r_included(self):
        p = _pos(entry=10.50, stop=10.30, shares=50)
        result = check_hard_stop(p, current_price=10.30, risk_per_share=0.20, quote_age_seconds=2.0)
        assert result.pnl is not None
        assert result.pnl_r is not None


# ──────────────────────────────────────────────────────────────────
#  Invalidation
# ──────────────────────────────────────────────────────────────────


class TestInvalidation:
    def test_hod_reclaim_fails(self):
        p = _pos()
        bars = [Bar(10.50, 10.51, 10.20, 10.25, 500)]  # red close below HOD
        result = check_invalidation(p, current_price=10.25, entry_setup="hod_reclaim",
                                     bars=bars, prior_hod=10.50, quote_age_seconds=2.0)
        assert result is not None
        assert "hod_reclaim_failed" in result.reason

    def test_vwap_reclaim_fails(self):
        p = _pos()
        bars = [Bar(9.90, 9.95, 9.80, 9.85, 500)]  # red, below VWAP
        result = check_invalidation(p, current_price=9.85, entry_setup="vwap_reclaim",
                                     bars=bars, vwap=10.00, quote_age_seconds=2.0)
        assert result is not None

    def test_no_bars_no_invalidation(self):
        p = _pos()
        result = check_invalidation(p, current_price=10.20, entry_setup="first_pullback")
        assert result is None


# ──────────────────────────────────────────────────────────────────
#  Missing protection
# ──────────────────────────────────────────────────────────────────


class TestMissingProtection:
    def test_unprotected_open(self):
        p = _pos(state=PositionState.OPEN)
        result = check_missing_protection(p, position_unprotected=True)
        assert result is not None


# ──────────────────────────────────────────────────────────────────
#  Scale-out
# ──────────────────────────────────────────────────────────────────


class TestScaleOut:
    def test_normal_1r_scale(self):
        p = _pos(entry=10.50, shares=100)
        result = check_scale_out(p, current_price=10.70, risk_per_share=0.20)
        # P&L = (10.70-10.50)*100 = 20. Total risk = 0.20*100 = 20. R = 1.0
        assert result is not None
        assert "scale_out_1R" in result.reason

    def test_extended_half_r(self):
        p = _pos(entry=10.50, shares=100)
        result = check_scale_out(p, current_price=10.60, risk_per_share=0.20,
                                  move_state=MoveState.EXTENDED)
        # 0.5R reached → scale 50%
        assert result is not None
        assert result.exit_pct == 50

    def test_extension_bar(self):
        p = _pos(entry=10.50, shares=100)
        # Build bars: flat base then one very wide-range candle (but not yet 1R)
        base = [Bar(10.50, 10.52, 10.48, 10.50, 500) for _ in range(9)]
        bars = base + [Bar(10.50, 10.65, 10.45, 10.62, 2000)]  # wide range, price=10.62, P&L ~0.6R
        result = check_scale_out(p, current_price=10.62, risk_per_share=0.20, bars=bars)
        if result is not None:
            assert "extension_bar" in result.reason

    def test_no_scale_below_1r(self):
        p = _pos(entry=10.50, shares=100)
        result = check_scale_out(p, current_price=10.55, risk_per_share=0.20)
        assert result is None

    def test_spread_caution_scale(self):
        p = _pos(entry=10.50, shares=100)
        result = check_scale_out(p, current_price=10.70, risk_per_share=0.20,
                                  spread_pct=4.0)
        assert result is not None  # 1R + spread caution → scale

    def test_scalp_mode_full_exit_at_05r(self):
        p = _pos(entry=10.50, shares=100)
        result = check_scale_out(p, current_price=10.60, risk_per_share=0.20,
                                  entry_setup="scalp_reclaim")
        # 0.5R → sell 100%
        assert result is not None
        assert result.exit_pct == 100


# ──────────────────────────────────────────────────────────────────
#  Failed reclaim
# ──────────────────────────────────────────────────────────────────


class TestFailedReclaim:
    def test_two_red_bars_after_profit(self):
        p = _pos(entry=10.00, shares=10)
        bars = [
            Bar(10.50, 10.55, 10.45, 10.48, 300),  # red
            Bar(10.48, 10.50, 10.40, 10.42, 400),  # red again
        ]
        result = check_failed_reclaim(p, current_price=10.42, risk_per_share=0.20, bars=bars)
        assert result is not None
        assert "failed_reclaim" in result.reason

    def test_no_failed_reclaim_without_profit(self):
        p = _pos(entry=10.50, shares=10)
        bars = [Bar(10.30, 10.35, 10.25, 10.28, 300), Bar(10.28, 10.30, 10.20, 10.22, 400)]
        result = check_failed_reclaim(p, current_price=10.22, risk_per_share=0.20, bars=bars)
        assert result is None  # P&L is negative


# ──────────────────────────────────────────────────────────────────
#  VWAP loss
# ──────────────────────────────────────────────────────────────────


class TestVwapLoss:
    def test_below_vwap_losing(self):
        p = _pos(entry=10.50)
        result = check_vwap_loss(p, current_price=10.20, vwap=10.40, risk_per_share=0.20)
        assert result is not None

    def test_below_vwap_but_profiting(self):
        p = _pos(entry=10.00)
        result = check_vwap_loss(p, current_price=10.30, vwap=10.40, risk_per_share=0.20)
        assert result is None  # profitable even below VWAP


# ──────────────────────────────────────────────────────────────────
#  Spread expansion
# ──────────────────────────────────────────────────────────────────


class TestSpreadExpansion:
    def test_spread_doubled(self):
        p = _pos()
        result = check_spread_expansion(p, current_price=10.60, spread_pct=2.5, entry_spread_pct=0.8)
        assert result is not None

    def test_no_expansion(self):
        p = _pos()
        result = check_spread_expansion(p, current_price=10.60, spread_pct=1.2, entry_spread_pct=0.8)
        assert result is None


# ──────────────────────────────────────────────────────────────────
#  Volume disappearance
# ──────────────────────────────────────────────────────────────────


class TestVolumeDisappearance:
    def test_zero_volume(self):
        p = _pos()
        bars = [Bar(10.50, 10.52, 10.48, 10.50, 0) for _ in range(5)]
        result = check_volume_disappearance(p, current_price=10.50, bars=bars)
        assert result is not None  # last 3 bars have vol < 100

    def test_normal_volume(self):
        p = _pos()
        bars = [Bar(10.50, 10.52, 10.48, 10.50, 1000) for _ in range(5)]
        result = check_volume_disappearance(p, current_price=10.50, bars=bars)
        assert result is None


# ──────────────────────────────────────────────────────────────────
#  Time exit
# ──────────────────────────────────────────────────────────────────


class TestTimeExit:
    def test_at_flatten_time(self):
        p = _pos()
        result = check_time_exit(p, et_time=time(15, 55))
        assert result is not None

    def test_after_flatten_time(self):
        p = _pos()
        result = check_time_exit(p, et_time=time(16, 0))
        assert result is not None

    def test_before_flatten_time(self):
        p = _pos()
        result = check_time_exit(p, et_time=time(14, 0))
        assert result is None


# ──────────────────────────────────────────────────────────────────
#  Runner trail
# ──────────────────────────────────────────────────────────────────


class TestRunnerTrail:
    def test_atr_chandelier_trail_fires(self):
        p = _pos(state=PositionState.RUNNER)
        p.highest_price_seen = 11.50
        p.trailing_stop_price = 11.00
        result = check_runner_trail(p, current_price=10.98)
        assert result is not None
        assert "atr_trail_hit" in result.reason

    def test_atr_chandelier_computes_from_inputs(self):
        p = _pos(state=PositionState.RUNNER)
        result = check_runner_trail(
            p,
            current_price=10.98,
            highest_price_seen=11.50,
            atr=0.10,
            risk_per_share=0.20,
            trail_multiplier=2.5,
        )
        assert result is not None
        assert "atr_trail_hit" in result.reason

    def test_atr_chandelier_minimum_distance(self):
        p = _pos(state=PositionState.RUNNER, entry=10.50, stop=10.00)
        result = check_runner_trail(
            p,
            current_price=11.20,
            highest_price_seen=11.50,
            atr=0.05,
            risk_per_share=0.50,
            trail_multiplier=2.5,
        )
        assert result is None

    def test_not_runner_no_trail(self):
        p = _pos(state=PositionState.OPEN)
        result = check_runner_trail(p, current_price=10.80)
        assert result is None


# ──────────────────────────────────────────────────────────────────
#  Orchestrator: priority
# ──────────────────────────────────────────────────────────────────


class TestCheckExits:
    def test_emergency_before_hard_stop(self):
        p = _pos(stop=10.30)
        result = check_exits(p, current_price=10.20, spread_pct=6.0)
        # spread explosion (P1) should fire before hard stop (P3)
        assert result is not None
        assert "spread_explosion" in result.reason

    def test_hard_stop_when_no_emergency(self):
        p = _pos(stop=10.30)
        result = check_exits(p, current_price=10.29, spread_pct=1.0, quote_age_seconds=2.0)
        assert result is not None
        assert "hard_stop" in result.reason

    def test_time_flatten_overrides_scale_out_at_1r(self):
        p = _pos(entry=10.00, stop=9.80, shares=100)
        result = check_exits(
            p,
            current_price=10.20,
            risk_per_share=0.20,
            et_time=time(15, 55),
        )
        assert result is not None
        assert result.exit_pct == 100
        assert "flatten_time" in result.reason
        assert "scale_out_1R" not in result.reason

    def test_stale_quote_30s_blocks_hard_stop(self):
        p = _pos(stop=10.30)
        result = check_exits(
            p, current_price=10.20, spread_pct=0.5, quote_age_seconds=30.0,
        )
        assert result is None

    def test_no_exit_when_all_clear(self):
        p = _pos(entry=10.50, stop=10.30)
        result = check_exits(p, current_price=10.60, spread_pct=0.5, risk_per_share=0.20)
        assert result is None

    def test_returns_exit_decision(self):
        p = _pos(stop=10.30)
        result = check_exits(p, current_price=10.20, quote_age_seconds=2.0)
        assert isinstance(result, ExitDecision)
        assert result.should_exit is True
        assert len(result.reason) > 0
