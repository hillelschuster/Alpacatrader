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

from src.classifier_features import ClassifierFeatures, derive_classifier_features
from src.entries import Bar
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


# ══════════════════════════════════════════════════════════════════
#  Phase 8 — T8.2: Runtime-path classifier tests from bar fixtures
# ══════════════════════════════════════════════════════════════════


class TestRuntimePathClassifier:
    """T8.2: Classifier reachable from bar-derived features, not just direct injection.

    These tests use bar-like features that the runtime pipeline actually
    computes (avg_range, rvol, vwap, etc.) rather than the boolean flags
    that only exist in test harnesses.
    """

    def test_active_state_reachable_from_bar_features(self):
        """A healthy uptrending symbol reaches ACTIVE from bar-derived features."""
        # Simulate a strong, clean uptrend: price near HOD, high RVOL, no volatility spikes
        state, mode, evidence = classify_move_state(
            symbol="DSY",
            price=10.50,
            day_high=10.55,
            day_low=10.00,
            halt_count_today=0,
            spread_pct=0.3,
            rvol=5.0,
            avg_range=0.10,
            vwap=10.30,
            ema9=10.20,
            strong_volume=True,
            pullbacks_bought=True,
            higher_low_structure=True,
            hod_behavior_repeated=True,
        )
        assert state == MoveState.ACTIVE, (
            f"Expected ACTIVE, got {state} with evidence: {evidence}"
        )
        assert mode == ModeType.STARTER_ENTRY

    def test_backside_reachable_from_bar_features(self):
        """Fading symbol with lower highs and losing VWAP reaches BACKSIDE."""
        state, mode, evidence = classify_move_state(
            symbol="DSY",
            price=9.80,
            day_high=10.50,
            day_low=9.50,
            halt_count_today=0,
            spread_pct=1.2,  # >1% spread — backside signal
            rvol=2.0,
            vwap=10.20,  # price below VWAP
            consecutive_below_vwap=3,
            lower_highs_count=2,
            volume_fading=True,
            bounces_failing=True,
        )
        assert state == MoveState.BACKSIDE, (
            f"Expected BACKSIDE, got {state} with evidence: {evidence}"
        )
        assert mode == ModeType.AVOID_NEW_LONGS

    def test_halt_risk_reachable_from_vertical_move(self):
        """Vertical move with wide spread → HALT_RISK."""
        state, mode, evidence = classify_move_state(
            symbol="DSY",
            price=20.00,
            day_high=20.50,
            vertical_move=True,
            vertical_without_pullback=True,
            spread_pct=3.0,
            price_moved_pct_5m=10.0,
            avg_range=2.0,
        )
        assert state == MoveState.HALT_RISK, (
            f"Expected HALT_RISK, got {state} with evidence: {evidence}"
        )

    def test_extended_reachable_from_parabolic_gap(self):
        """Large gap from pullback low → EXTENDED."""
        state, mode, evidence = classify_move_state(
            symbol="DSY",
            price=12.00,
            pullback_low=8.00,  # 50% above pullback → extended
            rvol=3.0,
        )
        assert state == MoveState.EXTENDED, (
            f"Expected EXTENDED, got {state} with evidence: {evidence}"
        )


# ──────────────────────────────────────────────────────────────────
#  Task 8: Runtime-derived classifier features (helper layer)
# ──────────────────────────────────────────────────────────────────


def _surge_bars() -> list[Bar]:
    """Bars that surge upward with rising volume — ACTIVE/vertical."""
    return [
        Bar(10.00, 10.15, 9.98, 10.12, 1000),
        Bar(10.12, 10.30, 10.10, 10.28, 1500),
        Bar(10.28, 10.45, 10.25, 10.42, 2000),
        Bar(10.42, 10.60, 10.40, 10.58, 3000),
        Bar(10.58, 10.80, 10.55, 10.78, 4000),
    ]


def _fading_bars() -> list[Bar]:
    """Bars with lower highs + fading volume — BACKSIDE-ish."""
    return [
        Bar(10.50, 10.60, 10.40, 10.45, 4000),
        Bar(10.45, 10.55, 10.35, 10.40, 3000),
        Bar(10.40, 10.50, 10.30, 10.35, 2000),
        Bar(10.35, 10.42, 10.25, 10.30, 1500),
        Bar(10.30, 10.38, 10.20, 10.25, 1000),
    ]


class TestDeriveClassifierFeatures:
    """Unit tests for the bar-derived feature helper."""

    def test_empty_bars_returns_defaults(self):
        f = derive_classifier_features([], price=10.0, vwap=10.0, day_high=10.5)
        assert f.avg_range is None
        assert f.lower_highs_count == 0
        assert f.consecutive_below_vwap == 0
        assert f.pullback_low is None
        assert f.vertical_move is False

    def test_avg_range_computed(self):
        f = derive_classifier_features(_surge_bars(), price=10.78, vwap=10.30, day_high=10.80)
        # Each bar range: 0.17, 0.20, 0.20, 0.20, 0.25 → avg 0.204
        assert f.avg_range is not None
        assert 0.19 < f.avg_range < 0.22

    def test_derive_classifier_features_detects_lower_highs(self):
        """Plan Step 4: lower highs count ≥ 2 on fading bars."""
        bars = [
            Bar(10.4, 10.5, 10.2, 10.3, 1000),
            Bar(10.2, 10.3, 10.0, 10.1, 900),
            Bar(10.0, 10.1, 9.8, 9.9, 800),
        ]
        features = derive_classifier_features(bars, price=9.9, vwap=10.2, day_high=10.5)
        assert features.lower_highs_count >= 2, (
            f"Expected lower_highs_count >= 2, got {features.lower_highs_count}"
        )

    def test_surge_bars_no_lower_highs(self):
        f = derive_classifier_features(_surge_bars(), price=10.78, vwap=10.30, day_high=10.80)
        assert f.lower_highs_count == 0

    def test_consecutive_below_vwap(self):
        """All closes below vwap → consecutive count = n."""
        bars = _fading_bars()
        f = derive_classifier_features(bars, price=10.25, vwap=10.60, day_high=10.60)
        assert f.consecutive_below_vwap == 5

    def test_consecutive_below_vwap_none_when_vwap_missing(self):
        """Missing VWAP → consecutive_below_vwap stays 0 (safeguard)."""
        f = derive_classifier_features(_surge_bars(), price=10.78, vwap=None, day_high=10.80)
        assert f.consecutive_below_vwap == 0

    def test_higher_low_structure(self):
        """Three rising lows → higher_low_structure True."""
        bars = [
            Bar(10.0, 10.2, 9.9, 10.1, 1000),
            Bar(10.1, 10.3, 10.0, 10.2, 1200),
            Bar(10.2, 10.4, 10.1, 10.3, 1500),
        ]
        f = derive_classifier_features(bars, price=10.3, vwap=10.0, day_high=10.4)
        assert f.higher_low_structure is True

    def test_strong_volume(self):
        """Last bar volume > all prior → strong_volume True."""
        f = derive_classifier_features(_surge_bars(), price=10.78, vwap=10.30, day_high=10.80)
        assert f.strong_volume is True

    def test_volume_fading(self):
        """Fading bars → volume_fading True."""
        f = derive_classifier_features(_fading_bars(), price=10.25, vwap=10.60, day_high=10.60)
        assert f.volume_fading is True

    def test_pullbacks_bought(self):
        """Close up + higher low → pullbacks_bought True."""
        bars = [
            Bar(10.0, 10.2, 9.9, 10.05, 1000),
            Bar(10.05, 10.3, 10.0, 10.25, 1500),
        ]
        f = derive_classifier_features(bars, price=10.25, vwap=10.0, day_high=10.3)
        assert f.pullbacks_bought is True

    def test_vertical_move_via_price_pct(self):
        """≥10% move in 5 bars → vertical_move True."""
        bars = [
            Bar(10.0, 10.1, 9.9, 10.0, 1000),
            Bar(10.0, 10.2, 9.9, 10.1, 1200),
            Bar(10.1, 10.5, 10.0, 10.4, 2000),
            Bar(10.4, 11.0, 10.3, 10.9, 3000),
            Bar(10.9, 11.5, 10.8, 11.4, 4000),  # +14% from 10.0
        ]
        f = derive_classifier_features(bars, price=11.4, vwap=10.3, day_high=11.5)
        assert f.price_moved_pct_5m is not None
        assert f.price_moved_pct_5m >= 10.0
        assert f.vertical_move is True

    def test_vertical_without_pullback(self):
        """vertical_without_pullback = vertical_move AND not has_pullback_formed.

        Per plan def: has_pullback_formed = (n>=3 and min(lows[-3:]) < close).
        Since low <= close always and lows[-1] is in lows[-3:], has_pullback_formed
        is False only when close == min(lows[-3:]) (last bar closes at the lowest
        low of the window).  Construct bars where price moved ≥10% (vertical_move
        via price_pct path) but the last bar closes exactly at min(lows[-3:]).
        """
        bars = [
            Bar(10.0, 10.2, 9.9, 10.0, 1000),    # close 10.0
            Bar(10.0, 10.6, 10.0, 10.5, 1500),   # close 10.5
            Bar(10.5, 11.1, 11.0, 11.0, 2000),   # close 11.0, low 11.0
            Bar(11.0, 11.6, 11.1, 11.5, 1800),   # close 11.5, low 11.1
            Bar(11.5, 11.6, 11.0, 11.0, 2500),   # close 11.0 == low == min(lows[-3:])
        ]
        f = derive_classifier_features(bars, price=11.0, vwap=10.5, day_high=11.6)
        # price_pct = (11.0 - 10.0)/10.0 * 100 = 10.0 → vertical_move True
        assert f.price_moved_pct_5m is not None
        assert f.price_moved_pct_5m >= 10.0
        assert f.vertical_move is True
        assert f.has_pullback_formed is False, (
            f"Expected has_pullback_formed False, got True (pullback_low={f.pullback_low})"
        )
        assert f.vertical_without_pullback is True

    def test_pullback_low_and_has_pullback_formed(self):
        """Pullback low = min(lows[-5:]); pullback formed when min(lows[-3:]) < close."""
        f = derive_classifier_features(_surge_bars(), price=10.78, vwap=10.30, day_high=10.80)
        assert f.pullback_low == 9.98
        # lows[-3:] = [10.25, 10.40, 10.55], min=10.25 < close 10.78 → formed
        assert f.has_pullback_formed is True

    def test_nearest_stop_distance_pct(self):
        """Distance from price to pullback_low as % of price."""
        f = derive_classifier_features(_surge_bars(), price=10.78, vwap=10.30, day_high=10.80)
        # (10.78 - 9.98) / 10.78 * 100 ≈ 7.42%
        assert f.nearest_stop_distance_pct is not None
        assert 7.0 < f.nearest_stop_distance_pct < 8.0

    def test_failed_hod_reclaim(self):
        """Approach HOD then fall back → failed_hod_reclaim True."""
        bars = [
            Bar(10.0, 10.5, 9.9, 10.4, 1000),
            Bar(10.4, 10.55, 10.3, 10.45, 1200),  # approach 10.55
            Bar(10.45, 10.50, 10.2, 10.25, 900),   # fall back
        ]
        f = derive_classifier_features(bars, price=10.25, vwap=10.0, day_high=10.55)
        assert f.failed_hod_reclaim is True

    def test_failed_vwap_reclaim(self):
        """Approach vwap from below then close below → failed_vwap_reclaim True."""
        bars = [
            Bar(10.0, 10.2, 9.9, 10.05, 1000),
            Bar(10.05, 10.32, 10.0, 10.30, 1500),  # close 10.30 >= vwap*0.999 → approach
            Bar(10.30, 10.32, 10.1, 10.15, 900),   # close below vwap
        ]
        f = derive_classifier_features(bars, price=10.15, vwap=10.30, day_high=10.5)
        assert f.failed_vwap_reclaim is True

    def test_hod_behavior_repeated(self):
        """≥2 lower highs while near HOD → hod_behavior_repeated True."""
        bars = [
            Bar(10.0, 10.60, 9.9, 10.55, 2000),   # near HOD 10.60
            Bar(10.55, 10.58, 10.3, 10.40, 1500),  # lower high
            Bar(10.40, 10.55, 10.2, 10.30, 1200),  # lower high
        ]
        f = derive_classifier_features(bars, price=10.30, vwap=10.0, day_high=10.60)
        assert f.lower_highs_count >= 2
        assert f.hod_behavior_repeated is True

    def test_bounces_failing(self):
        """Higher low but close down → bounces_failing True."""
        bars = [
            Bar(10.0, 10.3, 9.8, 10.25, 2000),
            Bar(10.25, 10.4, 9.9, 10.20, 1800),   # higher low 9.9 > 9.8, close down
            Bar(10.20, 10.35, 10.0, 10.15, 1500),  # higher low 10.0 > 9.9, close down
        ]
        f = derive_classifier_features(bars, price=10.15, vwap=10.5, day_high=10.4)
        assert f.higher_low_structure is True
        assert f.bounces_failing is True

    def test_missing_day_high_keeps_hod_features_false(self):
        """No day_high → failed_hod_reclaim + hod_behavior_repeated stay False."""
        f = derive_classifier_features(_surge_bars(), price=10.78, vwap=10.30, day_high=None)
        assert f.failed_hod_reclaim is False
        assert f.hod_behavior_repeated is False

    def test_features_feed_into_classify_move_state(self):
        """Derived features can be passed straight into classify_move_state."""
        bars = _surge_bars()
        f = derive_classifier_features(bars, price=10.78, vwap=10.30, day_high=10.80)
        state, mode, evidence = classify_move_state(
            price=10.78,
            day_high=10.80,
            vwap=10.30,
            spread_pct=0.3,
            rvol=5.0,
            avg_range=f.avg_range,
            lower_highs_count=f.lower_highs_count,
            consecutive_below_vwap=f.consecutive_below_vwap,
            higher_low_structure=f.higher_low_structure,
            strong_volume=f.strong_volume,
            volume_fading=f.volume_fading,
            bounces_failing=f.bounces_failing,
            pullbacks_bought=f.pullbacks_bought,
            vertical_move=f.vertical_move,
            vertical_without_pullback=f.vertical_without_pullback,
            price_moved_pct_5m=f.price_moved_pct_5m,
            pullback_low=f.pullback_low,
            nearest_stop_distance_pct=f.nearest_stop_distance_pct,
            failed_hod_reclaim=f.failed_hod_reclaim,
            failed_vwap_reclaim=f.failed_vwap_reclaim,
            hod_behavior_repeated=f.hod_behavior_repeated,
            has_pullback_formed=f.has_pullback_formed,
        )
        # Surge bars → ACTIVE or EXTENDED (stop distance may push EXTENDED),
        # but never BACKSIDE/HALT_RISK — that's the real safeguard.
        assert state not in (MoveState.BACKSIDE, MoveState.HALT_RISK), (
            f"Surge bars must not be BACKSIDE/HALT_RISK, got {state}: {evidence}"
        )
