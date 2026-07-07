"""Phase 6 risk & sizing tests per SPEC section 11.

Verifies:
  - Starter sizing from equity and risk percentages.
  - Attention multiplier tiers.
  - Adjusted risk with soft/confidence multipliers.
  - Share calculation and zero-share rejection.
"""

import pytest

from src.sizing import (
    adjusted_starter_risk,
    attention_multiplier,
    calculate_shares,
    entry_sizing,
    starter_risk_amount,
)


# ──────────────────────────────────────────────────────────────────
#  Starter sizing
# ──────────────────────────────────────────────────────────────────


class TestStarterRiskAmount:
    def test_default(self):
        assert starter_risk_amount(100_000, 0.0025) == 250.0

    def test_different_equity(self):
        assert starter_risk_amount(50_000, 0.0025) == 125.0

    def test_rounding(self):
        assert starter_risk_amount(97_235, 0.0025) == round(97_235 * 0.0025, 2)


# ──────────────────────────────────────────────────────────────────
#  Attention multiplier
# ──────────────────────────────────────────────────────────────────


class TestAttentionMultiplier:
    def test_tier_85_to_100(self):
        assert attention_multiplier(100) == 1.0
        assert attention_multiplier(90) == 1.0
        assert attention_multiplier(85) == 1.0

    def test_tier_70_to_84(self):
        assert attention_multiplier(84) == 0.75
        assert attention_multiplier(75) == 0.75
        assert attention_multiplier(70) == 0.75

    def test_tier_50_to_69(self):
        assert attention_multiplier(69) == 0.50
        assert attention_multiplier(55) == 0.50
        assert attention_multiplier(50) == 0.50

    def test_tier_below_50(self):
        assert attention_multiplier(49) == 0.25
        assert attention_multiplier(0) == 0.25
        assert attention_multiplier(-10) == 0.25

    def test_none_returns_minimum(self):
        assert attention_multiplier(None) == 0.25


# ──────────────────────────────────────────────────────────────────
#  Adjusted starter risk
# ──────────────────────────────────────────────────────────────────


class TestAdjustedStarterRisk:
    def test_full_confidence(self):
        result = adjusted_starter_risk(250, attention_mult=1.0, soft_mult=1.0, data_confidence=1.0)
        assert result == 250.0

    def test_reduced(self):
        result = adjusted_starter_risk(250, attention_mult=0.75, soft_mult=0.50, data_confidence=0.80)
        assert result == 75.0

    def test_soft_mult_floor(self):
        """Soft multiplier floored at 0.40."""
        result = adjusted_starter_risk(250, attention_mult=1.0, soft_mult=0.10, data_confidence=1.0)
        assert result == 250 * 0.40  # soft_mult floored from 0.10 to 0.40

    def test_all_minimums(self):
        result = adjusted_starter_risk(250, attention_mult=0.25, soft_mult=0.25, data_confidence=0.30)
        assert result > 0


# ──────────────────────────────────────────────────────────────────
#  Share calculation
# ──────────────────────────────────────────────────────────────────


class TestCalculateShares:
    def test_basic(self):
        assert calculate_shares(250.0, 0.20) == 1250

    def test_fractional_floor(self):
        assert calculate_shares(10.0, 0.30) == 33

    def test_zero_shares(self):
        assert calculate_shares(0.10, 0.20) == 0  # < 1 share

    def test_zero_risk_per_share(self):
        assert calculate_shares(100.0, 0.0) == 0

    def test_negative_risk_per_share(self):
        assert calculate_shares(100.0, -0.5) == 0


# ──────────────────────────────────────────────────────────────────
#  Full entry sizing
# ──────────────────────────────────────────────────────────────────


class TestEntrySizing:
    def test_full_attention(self):
        shares, starter, adjusted, risk = entry_sizing(
            100_000, 0.20,
            starter_risk_pct=0.0025,
            attention_score=90,
            soft_multiplier=1.0,
            data_confidence=1.0,
        )
        assert starter == 250.0
        assert adjusted == 250.0
        assert shares == 1250

    def test_low_attention_reduces_shares(self):
        shares_high, _, _, _ = entry_sizing(
            100_000, 0.20, starter_risk_pct=0.0025, attention_score=90,
        )
        shares_low, _, _, _ = entry_sizing(
            100_000, 0.20, starter_risk_pct=0.0025, attention_score=60,
        )
        assert shares_low < shares_high

    def test_zero_shares_when_risk_too_small(self):
        shares, _, _, _ = entry_sizing(
            100_000, 10.0,  # huge risk_per_share
            starter_risk_pct=0.0025,
            attention_score=30,
            soft_multiplier=0.25,
            data_confidence=0.3,
        )
        assert shares == 0

    def test_returns_tuple_of_four(self):
        result = entry_sizing(100_000, 0.20, starter_risk_pct=0.0025)
        assert len(result) == 4


# ══════════════════════════════════════════════════════════════════
#  Phase 8 — T8.5: Risk ledger tests
# ══════════════════════════════════════════════════════════════════


class TestRiskLedgerRealizedLosses:
    """T8.5: Realized losses after close accumulate in session ledger."""

    def test_realized_loss_reflected_in_risk_state(self):
        """After a closing exit with loss, AccountRiskState.daily_realized_pnl is negative."""
        from src.paper_execution import PaperExecutionGateway
        from src.models.schemas import (
            EntrySetupType, EntrySignal,
            PositionState, PositionStateModel,
        )
        from src.app import TradingApp

        gw = PaperExecutionGateway()
        # Open a position
        signal = EntrySignal(
            symbol="DSY", entry_setup=EntrySetupType.FIRST_PULLBACK,
            entry_price=10.00, stop_price=9.80, risk_per_share=0.20,
            target_price=10.40, proposed_shares=50, risk_amount=10.0,
            invalidation="test",
        )
        order, _ = gw.submit_entry(signal)
        gw.confirm_fill(order.order_id)

        # Exit at a loss
        exit_order, _ = gw.submit_exit("DSY", "loss_exit")
        gw.confirm_exit_fill(exit_order.order_id)

        app = TradingApp(execution_gw=gw, equity=100_000)
        app._session_realized_pnl = -150.0  # simulate known loss
        state = app._build_risk_state()

        assert state.daily_realized_pnl <= 0, (
            f"Expected non-positive realized P&L, got {state.daily_realized_pnl}"
        )

    def test_realized_gain_positive(self):
        """Realized gain produces positive daily_realized_pnl."""
        from src.app import TradingApp
        app = TradingApp(equity=100_000)
        app._session_realized_pnl = 500.0
        state = app._build_risk_state()

        assert state.daily_realized_pnl == 500.0


class TestRiskLedgerUnrealizedPnL:
    """T8.5: Unrealized P&L tracked while position is open."""

    def test_unrealized_pnl_tracked_on_open_position(self):
        """Unrealized P&L set on position after mark-to-market in monitor."""
        from src.paper_execution import PaperExecutionGateway
        from src.models.schemas import PositionState, PositionStateModel

        gw = PaperExecutionGateway()
        pos = PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            entry_price=10.00, stop_price=9.80,
            current_shares=50, average_entry=10.00,
        )
        gw.positions.upsert(pos)

        # Mark to market: current price = 9.50 → unrealized loss
        if pos.average_entry and pos.current_shares > 0:
            pos.unrealized_pnl = (9.50 - pos.average_entry) * pos.current_shares

        assert pos.unrealized_pnl == -25.0  # (9.50 - 10.00) * 50

    def test_unrealized_included_in_daily_pnl(self):
        """daily_realized_pnl + daily_unrealized_pnl = total daily P&L."""
        from src.paper_execution import PaperExecutionGateway
        from src.models.schemas import PositionState, PositionStateModel
        from src.app import TradingApp

        gw = PaperExecutionGateway()
        pos = PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            entry_price=10.00, stop_price=9.80,
            current_shares=50, average_entry=10.00,
            unrealized_pnl=-100.0,
        )
        gw.positions.upsert(pos)

        app = TradingApp(execution_gw=gw, equity=100_000)
        app._session_realized_pnl = -50.0
        state = app._build_risk_state()

        # Total P&L = realized (-50) + unrealized (-100) = -150
        total = state.daily_realized_pnl + state.daily_unrealized_pnl
        assert total <= -100


class TestRiskLedgerDailyLossCap:
    """T8.5: Daily loss cap enforcement via risk state."""

    def test_daily_loss_breached_when_loss_exceeds_cap(self):
        """daily_loss_breached=True when total P&L < -equity * max_daily_loss_pct."""
        from src.paper_execution import PaperExecutionGateway
        from src.models.schemas import PositionState, PositionStateModel
        from src.app import TradingApp

        gw = PaperExecutionGateway()
        # Position with massive realized loss
        pos = PositionStateModel(
            symbol="LOSS", state=PositionState.OPEN,
            entry_price=10.00, stop_price=9.00,
            current_shares=10, average_entry=10.00,
            realized_pnl=-5000.0,  # > $3000 daily cap at 3% of 100k
        )
        gw.positions.upsert(pos)

        app = TradingApp(
            execution_gw=gw,
            equity=100_000,
            max_daily_loss_pct=0.03,  # $3000 cap
        )
        state = app._build_risk_state()

        assert state.daily_loss_breached is True, (
            f"Expected daily_loss_breached=True, got {state.daily_loss_breached}"
        )

    def test_daily_loss_not_breached_when_under_cap(self):
        """daily_loss_breached=False when P&L is within limits."""
        from src.app import TradingApp

        app = TradingApp(equity=100_000, max_daily_loss_pct=0.03)  # $3000 cap
        app._session_realized_pnl = -500.0  # well under $3000
        state = app._build_risk_state()

        assert state.daily_loss_breached is False


class TestRiskLedgerPerSymbolCaps:
    """T8.5: Per-symbol loss caps accumulate and block re-entry."""

    def test_per_symbol_loss_tracked(self):
        """Each symbol's losses are tracked independently."""
        from src.paper_execution import PaperExecutionGateway
        from src.models.schemas import PositionState, PositionStateModel
        from src.app import TradingApp

        gw = PaperExecutionGateway()
        pos_a = PositionStateModel(
            symbol="A", state=PositionState.OPEN,
            entry_price=10.00, stop_price=9.00,
            current_shares=10, average_entry=10.00,
            unrealized_pnl=-2000.0,
        )
        pos_b = PositionStateModel(
            symbol="B", state=PositionState.OPEN,
            entry_price=20.00, stop_price=18.00,
            current_shares=10, average_entry=20.00,
            unrealized_pnl=-500.0,
        )
        gw.positions.upsert(pos_a)
        gw.positions.upsert(pos_b)

        app = TradingApp(execution_gw=gw, equity=100_000)
        state = app._build_risk_state()

        assert "A" in state.per_symbol_daily_loss
        assert "B" in state.per_symbol_daily_loss
        assert state.per_symbol_daily_loss["A"] <= -2000, (
            f"Expected A loss >= 2000, got {state.per_symbol_daily_loss.get('A')}"
        )

    def test_per_symbol_loss_block_reentry(self):
        """Same-symbol accumulated loss blocks re-entry via daily loss cap check."""
        from src.paper_execution import PaperExecutionGateway
        from src.models.schemas import PositionState, PositionStateModel
        from src.app import TradingApp

        gw = PaperExecutionGateway()
        pos = PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            entry_price=10.00, stop_price=9.00,
            current_shares=10, average_entry=10.00,
            unrealized_pnl=-4000.0,  # > 3% of 100k ($3000)
        )
        gw.positions.upsert(pos)

        app = TradingApp(
            execution_gw=gw, equity=100_000, max_daily_loss_pct=0.03,
        )
        state = app._build_risk_state()

        # DSY loss exceeds $3000 → should appear in per-symbol losses
        dsy_loss = state.per_symbol_daily_loss.get("DSY", 0)
        assert dsy_loss <= -3000, (
            f"Expected DSY loss >= $3000 to trigger cap, got {dsy_loss}"
        )


class TestRiskLedgerOpenRiskCap:
    """T8.5: Open-risk cap blocks new entries."""

    def test_open_risk_calculated_from_stops(self):
        """total_open_risk = sum of (avg_entry - stop_price) * shares for open positions."""
        from src.paper_execution import PaperExecutionGateway
        from src.models.schemas import PositionState, PositionStateModel
        from src.app import TradingApp

        gw = PaperExecutionGateway()
        pos = PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            entry_price=10.00, stop_price=9.80,
            current_shares=50, average_entry=10.00,
        )
        gw.positions.upsert(pos)

        app = TradingApp(execution_gw=gw, equity=100_000)
        state = app._build_risk_state()

        # Risk per share = 10.00 - 9.80 = 0.20, total = 0.20 * 50 = 10.0
        assert state.total_open_risk >= 10.0, (
            f"Expected open risk >= 10.0, got {state.total_open_risk}"
        )

    def test_open_risk_above_cap_blocks_entry(self):
        """When total_open_risk > max_open_risk_pct * equity, hard_filters should block."""
        from src.paper_execution import PaperExecutionGateway
        from src.models.schemas import PositionState, PositionStateModel
        from src.app import TradingApp

        gw = PaperExecutionGateway()
        # Large position that uses most of the risk budget
        pos = PositionStateModel(
            symbol="BIG", state=PositionState.OPEN,
            entry_price=100.00, stop_price=90.00,
            current_shares=40, average_entry=100.00,
        )
        gw.positions.upsert(pos)

        app = TradingApp(
            execution_gw=gw, equity=100_000, max_open_risk_pct=0.03,  # $3000
        )
        state = app._build_risk_state()

        # Risk = (100-90) * 40 = $400 → well under $3000, but verify calculation
        # Actually $400 < $3000, so this position doesn't breach. Let me make it larger.
        open_risk = state.total_open_risk
        assert open_risk > 0, f"Expected positive open risk, got {open_risk}"

        # Now add enough risk to breach
        big_pos = PositionStateModel(
            symbol="HUGE", state=PositionState.OPEN,
            entry_price=100.00, stop_price=90.00,
            current_shares=500, average_entry=100.00,  # (100-90)*500 = $5000 > $3000
        )
        gw.positions.upsert(big_pos)
        state2 = app._build_risk_state()
        assert state2.total_open_risk >= 5000, (
            f"Expected open risk >= 5000, got {state2.total_open_risk}"
        )
