"""Phase 4 move-classifier tests per SPEC section 9.

Verifies:
  - Each state is detected correctly from its defining signals.
  - Priority order: halt-risk > backside > extended > active > early.
  - Mode mapping: each state → correct ModeType.
  - Evidence lists are populated with human-readable strings.
  - Entry-permission matrix matches SPEC §9.4.
  - Edge cases: missing data, threshold boundaries.
"""

import pytest

from src.move_classifier import (
    classify_move_state,
    setup_allowed,
    state_mode,
    _is_halt_risk,
    _is_backside,
    _is_extended,
    _is_active,
    _can_reclaim,
)
from src.models.schemas import ModeType, MoveState


# ──────────────────────────────────────────────────────────────────
#  Halt-risk detection
# ──────────────────────────────────────────────────────────────────


class TestHaltRisk:
    def test_halted_today(self):
        detected, evidence = _is_halt_risk(halt_count_today=1)
        assert detected is True
        assert any("halt_count=1" in e for e in evidence)

    def test_no_halt_no_signal(self):
        detected, _ = _is_halt_risk(halt_count_today=0)
        assert detected is False

    def test_spread_and_vertical(self):
        detected, evidence = _is_halt_risk(spread_pct=4.0, vertical_move=True)
        assert detected is True
        assert any("spread_gt_3pct" in e for e in evidence)

    def test_spread_only_no_detect(self):
        detected, _ = _is_halt_risk(spread_pct=4.0, vertical_move=False)
        assert detected is False

    def test_vertical_only_no_detect(self):
        detected, _ = _is_halt_risk(spread_pct=1.0, vertical_move=True)
        assert detected is False

    def test_moved_10pct_in_5min(self):
        detected, evidence = _is_halt_risk(price_moved_pct_5m=12.0)
        assert detected is True
        assert any("moved_gt_10pct" in e for e in evidence)

    def test_moved_under_10pct_no_detect(self):
        detected, _ = _is_halt_risk(price_moved_pct_5m=8.0)
        assert detected is False

    def test_quote_instability(self):
        detected, evidence = _is_halt_risk(quote_instability=True)
        assert detected is True
        assert any("quote_instability" in e for e in evidence)

    def test_vertical_without_pullback(self):
        detected, evidence = _is_halt_risk(vertical_without_pullback=True)
        assert detected is True

    def test_multiple_signals(self):
        detected, evidence = _is_halt_risk(halt_count_today=2, spread_pct=5.0, vertical_move=True)
        assert detected is True
        assert len(evidence) >= 2


# ──────────────────────────────────────────────────────────────────
#  Backside / fading detection
# ──────────────────────────────────────────────────────────────────


class TestBackside:
    def test_lower_highs_with_failed_hod(self):
        detected, evidence = _is_backside(lower_highs_count=3, failed_hod_reclaim=True)
        assert detected is True
        assert any("lower_highs=3" in e for e in evidence)

    def test_lower_highs_without_failed_hod_no_detect(self):
        detected, _ = _is_backside(lower_highs_count=3, failed_hod_reclaim=False)
        assert detected is False

    def test_below_vwap_with_failed_reclaim(self):
        detected, evidence = _is_backside(
            consecutive_below_vwap=6, failed_vwap_reclaim=True,
        )
        assert detected is True
        assert any("below_vwap=6_bars" in e for e in evidence)

    def test_below_vwap_without_failed_reclaim_no_detect(self):
        detected, _ = _is_backside(consecutive_below_vwap=6, failed_vwap_reclaim=False)
        assert detected is False

    def test_below_vwap_fewer_than_5_bars(self):
        detected, _ = _is_backside(consecutive_below_vwap=4, failed_vwap_reclaim=True)
        assert detected is False

    def test_volume_fading_and_bounces_failing(self):
        detected, evidence = _is_backside(volume_fading=True, bounces_failing=True)
        assert detected is True

    def test_volume_fading_alone_no_detect(self):
        detected, _ = _is_backside(volume_fading=True, bounces_failing=False)
        assert detected is False

    def test_spread_widening_no_reclaim(self):
        detected, evidence = _is_backside(spread_pct=2.0, price=9.50, vwap=10.00)
        assert detected is True  # spread > 1.0, price < vwap, can't reclaim

    def test_spread_widening_but_can_reclaim(self):
        detected, _ = _is_backside(spread_pct=2.0, price=9.90, vwap=10.00)
        assert detected is False  # within 2% of VWAP → reclaimable

    def test_no_signals(self):
        detected, _ = _is_backside()
        assert detected is False


# ──────────────────────────────────────────────────────────────────
#  Extended / parabolic detection
# ──────────────────────────────────────────────────────────────────


class TestExtended:
    def test_stop_distance_exceeds_max(self):
        detected, evidence = _is_extended(
            nearest_stop_distance_pct=6.0, max_stop_width_pct=5.0,
        )
        assert detected is True
        assert any("stop_distance=" in e for e in evidence)

    def test_stop_distance_within_max_no_detect(self):
        detected, _ = _is_extended(nearest_stop_distance_pct=3.0, max_stop_width_pct=5.0)
        assert detected is False

    def test_price_far_above_pullback_low(self):
        detected, evidence = _is_extended(price=12.0, pullback_low=10.0)
        assert detected is True  # (12-10)/10 = 20% > 15%

    def test_price_not_far_above_pullback(self):
        detected, _ = _is_extended(price=11.0, pullback_low=10.0)
        assert detected is False  # 10% < 15%

    def test_candle_range_gt_2x_avg(self):
        detected, evidence = _is_extended(candle_range_gt_2x_avg=True)
        assert detected is True

    def test_vertical_without_pullback_no_formed(self):
        detected, evidence = _is_extended(
            vertical_without_pullback=True, has_pullback_formed=False,
        )
        assert detected is True

    def test_vertical_but_pullback_formed_no_detect(self):
        detected, _ = _is_extended(
            vertical_without_pullback=True, has_pullback_formed=True,
        )
        assert detected is False

    def test_no_signals(self):
        detected, _ = _is_extended()
        assert detected is False


# ──────────────────────────────────────────────────────────────────
#  Active detection
# ──────────────────────────────────────────────────────────────────


class TestActive:
    def test_all_signals_active(self):
        detected, evidence = _is_active(
            hod_behavior_repeated=True,
            higher_low_structure=True,
            pullbacks_bought=True,
            strong_volume=True,
            spread_pct=1.0,
            nearest_stop_distance_pct=2.0,
            max_stop_width_pct=5.0,
        )
        assert detected is True
        assert len(evidence) >= 5

    def test_three_of_five_signals_active(self):
        detected, _ = _is_active(
            hod_behavior_repeated=True,
            higher_low_structure=True,
            pullbacks_bought=True,
            strong_volume=False,
            spread_pct=None,  # unknown
            nearest_stop_distance_pct=None,  # unknown
        )
        assert detected is True

    def test_two_signals_not_enough(self):
        detected, _ = _is_active(
            hod_behavior_repeated=True,
            higher_low_structure=True,
            pullbacks_bought=False,
            strong_volume=False,
            spread_pct=None,
            nearest_stop_distance_pct=None,
        )
        assert detected is False

    def test_spread_too_wide(self):
        detected, evidence = _is_active(
            hod_behavior_repeated=True,
            higher_low_structure=True,
            pullbacks_bought=True,
            strong_volume=True,
            spread_pct=4.0,
            nearest_stop_distance_pct=2.0,
            max_stop_width_pct=5.0,
        )
        # spread at 4.0 → not manageable (doesn't contribute to core_signals)
        # core: hod + hl + pb + vol + stop = 5, still ≥3 → active
        assert detected is True
        assert any("spread_wide=4.0" in e for e in evidence)

    def test_rvol_as_strong_volume(self):
        detected, _ = _is_active(
            hod_behavior_repeated=True,
            higher_low_structure=True,
            rvol=3.0,  # ≥2.0 → strong volume
        )
        assert detected is True

    def test_default_no_signals(self):
        detected, _ = _is_active()
        assert detected is False


# ──────────────────────────────────────────────────────────────────
#  can_reclaim helper
# ──────────────────────────────────────────────────────────────────


class TestCanReclaim:
    def test_within_2pct(self):
        assert _can_reclaim(9.90, 10.00) is True

    def test_outside_2pct(self):
        assert _can_reclaim(9.50, 10.00) is False

    def test_none_values(self):
        assert _can_reclaim(None, 10.0) is False
        assert _can_reclaim(10.0, None) is False

    def test_vwap_zero(self):
        assert _can_reclaim(10.0, 0.0) is False


# ──────────────────────────────────────────────────────────────────
#  Priority order (integration)
# ──────────────────────────────────────────────────────────────────


class TestPriorityOrder:
    def test_halt_risk_beats_backside(self):
        state, mode, evidence = classify_move_state(
            halt_count_today=1,  # halt-risk trigger
            lower_highs_count=3, failed_hod_reclaim=True,  # backside trigger
        )
        assert state == MoveState.HALT_RISK

    def test_backside_beats_extended(self):
        state, mode, evidence = classify_move_state(
            lower_highs_count=3, failed_hod_reclaim=True,  # backside
            candle_range_gt_2x_avg=True,  # extended
        )
        assert state == MoveState.BACKSIDE

    def test_extended_beats_active(self):
        state, mode, evidence = classify_move_state(
            nearest_stop_distance_pct=6.0, max_stop_width_pct=5.0,  # extended
            hod_behavior_repeated=True, higher_low_structure=True, pullbacks_bought=True,  # active
            strong_volume=True, spread_pct=1.0,  # active signals
        )
        assert state == MoveState.EXTENDED

    def test_active_beats_early(self):
        state, mode, evidence = classify_move_state(
            hod_behavior_repeated=True, higher_low_structure=True,
            pullbacks_bought=True, strong_volume=True,
            spread_pct=1.0,
        )
        assert state == MoveState.ACTIVE


# ──────────────────────────────────────────────────────────────────
#  Mode mapping
# ──────────────────────────────────────────────────────────────────


class TestModeMapping:
    def test_halt_risk_mode(self):
        _, mode, _ = classify_move_state(halt_count_today=1)
        assert mode == ModeType.AVOID_NEW_LONGS

    def test_backside_mode(self):
        _, mode, _ = classify_move_state(
            lower_highs_count=3, failed_hod_reclaim=True,
        )
        assert mode == ModeType.AVOID_NEW_LONGS

    def test_extended_mode(self):
        _, mode, _ = classify_move_state(candle_range_gt_2x_avg=True)
        assert mode == ModeType.SCALP_ONLY

    def test_active_mode(self):
        _, mode, _ = classify_move_state(
            hod_behavior_repeated=True, higher_low_structure=True,
            pullbacks_bought=True, strong_volume=True, spread_pct=1.0,
        )
        assert mode == ModeType.STARTER_ENTRY

    def test_early_mode(self):
        _, mode, _ = classify_move_state()
        assert mode == ModeType.WATCH

    def test_state_mode_utility(self):
        assert state_mode(MoveState.HALT_RISK) == ModeType.AVOID_NEW_LONGS
        assert state_mode(MoveState.BACKSIDE) == ModeType.AVOID_NEW_LONGS
        assert state_mode(MoveState.EXTENDED) == ModeType.SCALP_ONLY
        assert state_mode(MoveState.ACTIVE) == ModeType.STARTER_ENTRY
        assert state_mode(MoveState.EARLY) == ModeType.WATCH


# ──────────────────────────────────────────────────────────────────
#  Evidence lists
# ──────────────────────────────────────────────────────────────────


class TestEvidence:
    def test_early_returns_evidence(self):
        _, mode, evidence = classify_move_state(
            appeared_recently=True, attention_building=True,
        )
        assert mode == ModeType.WATCH
        assert len(evidence) >= 1
        assert "appeared_recently" in evidence

    def test_active_returns_evidence(self):
        state, mode, evidence = classify_move_state(
            hod_behavior_repeated=True, higher_low_structure=True,
            pullbacks_bought=True, strong_volume=True, spread_pct=1.0,
        )
        assert "hod_repeated" in evidence
        assert "higher_lows" in evidence

    def test_halt_risk_returns_evidence(self):
        state, mode, evidence = classify_move_state(halt_count_today=1)
        assert any("halt_count" in e for e in evidence)

    def test_evidence_never_empty(self):
        for _ in range(5):
            _, _, evidence = classify_move_state()
            assert len(evidence) > 0  # at least "default_early"


# ──────────────────────────────────────────────────────────────────
#  Entry permission matrix
# ──────────────────────────────────────────────────────────────────


class TestEntryPermissionMatrix:
    """Verify SPEC §9.4 entry permission matrix."""

    def test_early_permissions(self):
        assert setup_allowed(MoveState.EARLY, "first_pullback") is True
        assert setup_allowed(MoveState.EARLY, "vwap_reclaim") is True
        assert setup_allowed(MoveState.EARLY, "micro_pullback") is False
        assert setup_allowed(MoveState.EARLY, "hod_reclaim") is False
        assert setup_allowed(MoveState.EARLY, "consolidation_breakout") is False
        assert setup_allowed(MoveState.EARLY, "scalp_reclaim") is False

    def test_active_permissions(self):
        assert setup_allowed(MoveState.ACTIVE, "first_pullback") is True
        assert setup_allowed(MoveState.ACTIVE, "micro_pullback") is True
        assert setup_allowed(MoveState.ACTIVE, "hod_reclaim") is True
        assert setup_allowed(MoveState.ACTIVE, "consolidation_breakout") is True
        assert setup_allowed(MoveState.ACTIVE, "vwap_reclaim") is True
        assert setup_allowed(MoveState.ACTIVE, "scalp_reclaim") is False

    def test_extended_permissions(self):
        assert setup_allowed(MoveState.EXTENDED, "first_pullback") is True
        assert setup_allowed(MoveState.EXTENDED, "hod_reclaim") is True
        assert setup_allowed(MoveState.EXTENDED, "vwap_reclaim") is True
        assert setup_allowed(MoveState.EXTENDED, "scalp_reclaim") is True
        assert setup_allowed(MoveState.EXTENDED, "micro_pullback") is False
        assert setup_allowed(MoveState.EXTENDED, "consolidation_breakout") is False

    def test_backside_permissions(self):
        assert setup_allowed(MoveState.BACKSIDE, "vwap_reclaim") is True
        assert setup_allowed(MoveState.BACKSIDE, "first_pullback") is False
        assert setup_allowed(MoveState.BACKSIDE, "hod_reclaim") is False

    def test_halt_risk_permissions(self):
        assert setup_allowed(MoveState.HALT_RISK, "scalp_reclaim") is True
        assert setup_allowed(MoveState.HALT_RISK, "first_pullback") is False
        assert setup_allowed(MoveState.HALT_RISK, "vwap_reclaim") is False

    def test_unknown_setup_returns_false(self):
        assert setup_allowed(MoveState.ACTIVE, "nonexistent_setup") is False


# ──────────────────────────────────────────────────────────────────
#  Edge cases
# ──────────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_all_none_params_preserves_early(self):
        state, mode, evidence = classify_move_state()
        assert state == MoveState.EARLY
        assert mode == ModeType.WATCH

    def test_symbol_passed_through(self):
        """Symbol is accepted but doesn't affect classification."""
        state1, _, _ = classify_move_state(symbol="DSY")
        state2, _, _ = classify_move_state(symbol="AAPL")
        assert state1 == state2 == MoveState.EARLY

    def test_custom_thresholds_applied(self):
        """max_stop_width_pct is configurable."""
        state, _, evidence = classify_move_state(
            nearest_stop_distance_pct=4.0,
            max_stop_width_pct=3.0,  # lower than default 5.0
        )
        assert state == MoveState.EXTENDED  # 4.0 > 3.0 custom threshold

    def test_multi_day_runner_does_not_force_state(self):
        """Multi-day runner is context, not a state by itself."""
        state, _, _ = classify_move_state(
            multi_day_runner=True,
            hod_behavior_repeated=True, higher_low_structure=True,
            pullbacks_bought=True, strong_volume=True, spread_pct=1.0,
        )
        assert state == MoveState.ACTIVE  # still active with good signals

    def test_prior_state_does_not_override(self):
        """Prior state is informational, not deterministic."""
        state, _, _ = classify_move_state(
            prior_state="active",  # prior was active
            halt_count_today=1,    # now halted
        )
        assert state == MoveState.HALT_RISK  # current state wins


# ──────────────────────────────────────────────────────────────────
#  DSY-like candidate — not backside, maybe early/active
# ──────────────────────────────────────────────────────────────────


class TestDSYClassifier:
    """DSY-like: top gainer, Chinese, no news, active volume — should NOT
    be classified as backside or halt-risk just because it's speculative."""

    def test_dsy_as_early_with_building_attention(self):
        state, mode, evidence = classify_move_state(
            appeared_recently=True, attention_building=True, volume_improving=True,
        )
        assert state == MoveState.EARLY
        assert mode == ModeType.WATCH
        assert "appeared_recently" in evidence
        # Halt-risk should NOT be triggered — no halt signals
        assert state != MoveState.HALT_RISK

    def test_dsy_as_active_with_strong_volume(self):
        state, mode, _ = classify_move_state(
            hod_behavior_repeated=True, higher_low_structure=True,
            pullbacks_bought=True, strong_volume=True, rvol=5.0,
            spread_pct=0.8,
        )
        assert state == MoveState.ACTIVE
        assert mode == ModeType.STARTER_ENTRY

    def test_dsy_extended_when_parabolic(self):
        state, mode, _ = classify_move_state(
            price=15.00, pullback_low=10.00,  # 50% above pullback
        )
        assert state == MoveState.EXTENDED
        assert mode == ModeType.SCALP_ONLY

    def test_dsy_not_backside_with_volume(self):
        """High volume + no lower highs = not backside."""
        state, mode, _ = classify_move_state(
            hod_behavior_repeated=True, higher_low_structure=True,
            pullbacks_bought=True, strong_volume=True, rvol=8.0,
            spread_pct=0.8,
        )
        assert state != MoveState.BACKSIDE  # should be active, not fading
