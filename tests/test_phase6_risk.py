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
        """Soft multiplier floored at 0.25."""
        result = adjusted_starter_risk(250, attention_mult=1.0, soft_mult=0.10, data_confidence=1.0)
        assert result == 250 * 0.25  # soft_mult floored from 0.10 to 0.25

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
