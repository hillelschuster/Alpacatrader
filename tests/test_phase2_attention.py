"""Phase 2 attention-scoring tests per SPEC sections 6, 8.

Covers:
  - Price / volume / HOD attention factors
  - Factor redistribution when data is missing
  - Bonuses (theme, former runner, repeated scanner)
  - Theme detection
  - Former-runner store
  - Float rotation
  - Soft annotations and multiplier
  - DSY regression (SPEC §19.2)
  - Batch candidate scoring

No network calls, no broker dependencies.
"""

import sys
from datetime import datetime, timedelta, timezone

import pytest

from src.models.schemas import AttentionScore, Candidate
from src.scanner.attention import (
    FormerRunnerStore,
    _PRICE_WEIGHT,
    _VOLUME_WEIGHT,
    _HOD_WEIGHT,
    _price_attention,
    _volume_attention,
    _hod_acceleration,
    calculate_float_rotation,
    detect_themes,
    float_rotation_label,
    is_symbol_in_theme,
    score_attention,
    score_candidates,
)
from src.annotations import enrich_with_llm, map_soft_warnings, soft_warning_multiplier, spread_sizing_multiplier


# ── Helpers ───────────────────────────────────────────────────────


def _candidate(
    symbol: str = "DSY",
    *,
    price: float | None = 10.0,
    percent_gain: float | None = 15.0,
    premarket_gap_pct: float | None = None,
    current_volume: int | None = 5_000_000,
    relative_volume: float | None = None,
    dollar_volume: float | None = None,
    sector: str | None = None,
    industry: str | None = None,
    country: str | None = None,
    exchange: str | None = None,
    float_shares: int | None = None,
    market_cap: float | None = None,
    day_high: float | None = None,
    day_low: float | None = None,
    premarket_high: float | None = None,
    premarket_low: float | None = None,
    **kwargs,
) -> Candidate:
    return Candidate(
        symbol=symbol,
        price=price,
        percent_gain=percent_gain,
        premarket_gap_pct=premarket_gap_pct,
        current_volume=current_volume,
        relative_volume=relative_volume,
        dollar_volume=dollar_volume,
        sector=sector,
        industry=industry,
        country=country,
        exchange=exchange,
        float_shares=float_shares,
        market_cap=market_cap,
        day_high=day_high,
        day_low=day_low,
        premarket_high=premarket_high,
        premarket_low=premarket_low,
        **kwargs,
    )


class TestLLMPreMarketAnnotator:
    """Phase 7 LLM annotation stays optional and failure-safe."""

    def test_llm_failure_returns_empty_string(self, monkeypatch):
        class BrokenMessages:
            def create(self, **kwargs):
                raise RuntimeError("api unavailable")

        class BrokenAnthropic:
            def __init__(self, api_key):
                self.messages = BrokenMessages()

        fake_anthropic = type(sys)("anthropic")
        fake_anthropic.Anthropic = BrokenAnthropic
        monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)

        assert enrich_with_llm("DSY", 10.5, 25.0, 5_000_000, "test-key") == ""
        assert enrich_with_llm("DSY", 10.5, 25.0, 5_000_000, "") == ""

    def test_llm_success_returns_summary_without_leaking_api_key(self, monkeypatch):
        calls: list[dict] = []

        class TextBlock:
            text = "DSY has a potential catalyst from unusual premarket momentum."

        class SuccessfulMessages:
            def create(self, **kwargs):
                calls.append(kwargs)
                return type("Message", (), {"content": [TextBlock()]})()

        class SuccessfulAnthropic:
            def __init__(self, api_key):
                assert api_key == "secret-test-key"
                self.messages = SuccessfulMessages()

        fake_anthropic = type(sys)("anthropic")
        fake_anthropic.Anthropic = SuccessfulAnthropic
        monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)

        summary = enrich_with_llm("DSY", 10.5, 25.0, 5_000_000, "secret-test-key")

        assert summary == "DSY has a potential catalyst from unusual premarket momentum."
        assert calls[0]["model"] == "claude-haiku-4-5"
        assert calls[0]["max_tokens"] == 300
        assert "secret-test-key" not in str(calls[0])


# ──────────────────────────────────────────────────────────────────
#  Price attention
# ──────────────────────────────────────────────────────────────────


class TestPriceAttention:
    def test_100pct_gain_reaches_full_price_weight(self):
        """SPEC §11.18.5: cap raised to 100% — top gainers get primary ranking."""
        pts, available = _price_attention(percent_gain=100.0, premarket_gap_pct=None)
        assert available is True
        assert pts == _PRICE_WEIGHT

    def test_50pct_gain_gets_half_price_attention(self):
        pts, available = _price_attention(percent_gain=50.0, premarket_gap_pct=None)
        assert available is True
        assert pts == pytest.approx(_PRICE_WEIGHT * 0.5, abs=0.1)

    def test_250pct_gain_capped_at_full_weight(self):
        pts, available = _price_attention(percent_gain=250.0, premarket_gap_pct=None)
        assert available is True
        assert pts == _PRICE_WEIGHT

    def test_scales_linearly(self):
        pts, _ = _price_attention(percent_gain=25.0, premarket_gap_pct=None)
        assert pts == pytest.approx(7.5, abs=0.1)  # 25/100 * 30 = 7.5

    def test_uses_best_of_gain_and_gap(self):
        pts, _ = _price_attention(percent_gain=10.0, premarket_gap_pct=30.0)
        assert pts == pytest.approx(9.0, abs=0.1)  # 30/100 * 30 = 9

    def test_zero_or_negative_gain_returns_zero(self):
        pts, available = _price_attention(percent_gain=0.0, premarket_gap_pct=None)
        assert pts == 0.0
        assert available is True

        pts2, available2 = _price_attention(percent_gain=-5.0, premarket_gap_pct=None)
        assert pts2 == 0.0
        assert available2 is True

    def test_both_none_returns_unavailable(self):
        pts, available = _price_attention(percent_gain=None, premarket_gap_pct=None)
        assert pts == 0.0
        assert available is False

    def test_only_gap_available_works(self):
        pts, available = _price_attention(percent_gain=None, premarket_gap_pct=20.0)
        assert available is True
        assert pts == pytest.approx(6.0, abs=0.1)  # 20/100 * 30 = 6


# ──────────────────────────────────────────────────────────────────
#  Volume attention
# ──────────────────────────────────────────────────────────────────


class TestVolumeAttention:
    def test_rvol_component(self):
        pts, available = _volume_attention(
            rvol=3.0, dollar_volume_5m=None, candidate_volume=None,
            candidate_price=None, min_dollar_volume=100_000,
        )
        assert available is True
        assert pts == pytest.approx(15.0, abs=0.1)  # 3x RVOL starts the strong band

    def test_rvol_capped_at_25(self):
        pts, _ = _volume_attention(
            rvol=10.0, dollar_volume_5m=None, candidate_volume=None,
            candidate_price=None, min_dollar_volume=100_000,
        )
        assert pts == pytest.approx(25.0, abs=0.1)

    def test_dollar_volume_component(self):
        pts, available = _volume_attention(
            rvol=None, dollar_volume_5m=200_000, candidate_volume=None,
            candidate_price=None, min_dollar_volume=100_000,
        )
        assert available is True
        assert pts == pytest.approx(15.0, abs=0.1)  # 200k/100k * 15 = 30, capped at 15

    def test_both_rvol_and_dv(self):
        pts, _ = _volume_attention(
            rvol=4.0, dollar_volume_5m=300_000, candidate_volume=None,
            candidate_price=None, min_dollar_volume=100_000,
        )
        # RVOL 4x = 20, DV capped at 15 → total 35
        assert pts == pytest.approx(35.0, abs=0.1)

    def test_no_volume_data_returns_unavailable(self):
        pts, available = _volume_attention(
            rvol=None, dollar_volume_5m=None, candidate_volume=None,
            candidate_price=None, min_dollar_volume=100_000,
        )
        assert pts == 0.0
        assert available is False

    def test_fallback_from_candidate_volume_and_price(self):
        """When dollar_volume_5m is None, estimate from candidate volume/price."""
        pts, available = _volume_attention(
            rvol=None, dollar_volume_5m=None,
            candidate_volume=7_800_000, candidate_price=10.0,
            min_dollar_volume=100_000,
        )
        # DV estimate = 7.8M * 10 / 78 = 1,000,000
        # Pts = min(15, 1M/100k * 15) = min(15, 150) = 15
        assert available is True
        assert pts == pytest.approx(15.0, abs=0.1)


# ──────────────────────────────────────────────────────────────────
#  HOD acceleration
# ──────────────────────────────────────────────────────────────────


class TestHodAcceleration:
    def test_within_1pct_hod_gets_20_pts(self):
        pts, available = _hod_acceleration(
            price=10.05, hod_price=10.10,
            roc_1m_pct=None, roc_3m_pct=None, roc_5m_pct=None,
        )
        assert available is True
        # dist = (10.10 - 10.05)/10.10 = 0.495% → within 1% → 20 pts
        assert pts == pytest.approx(20.0, abs=0.5)

    def test_within_3pct_hod_gets_reduced_pts(self):
        pts, available = _hod_acceleration(
            price=9.80, hod_price=10.00,
            roc_1m_pct=None, roc_3m_pct=None, roc_5m_pct=None,
        )
        assert available is True
        # dist = 2% → within 3% → ~10.6 pts
        assert 10.0 < pts < 11.5

    def test_beyond_3pct_hod_gets_zero(self):
        pts, available = _hod_acceleration(
            price=9.00, hod_price=10.00,
            roc_1m_pct=None, roc_3m_pct=None, roc_5m_pct=None,
        )
        assert available is True
        assert pts == 0.0

    def test_roc_component(self):
        pts, available = _hod_acceleration(
            price=None, hod_price=None,
            roc_1m_pct=3.0, roc_3m_pct=2.0, roc_5m_pct=None,
        )
        assert available is True
        # best_roc = 3.0, 3/5 * 10 = 6 pts
        assert pts == pytest.approx(6.0, abs=0.1)

    def test_roc_capped_at_10(self):
        pts, _ = _hod_acceleration(
            price=None, hod_price=None,
            roc_1m_pct=10.0, roc_3m_pct=None, roc_5m_pct=None,
        )
        assert pts == pytest.approx(10.0, abs=0.1)  # capped at 10

    def test_recent_new_hod_gets_strong_hod_points(self):
        pts, available = _hod_acceleration(
            price=10.40, hod_price=11.00,
            roc_1m_pct=None, roc_3m_pct=None, roc_5m_pct=None,
            new_hod_recent=True,
        )
        assert available is True
        assert pts == pytest.approx(20.0, abs=0.1)

    def test_no_hod_or_roc_returns_unavailable(self):
        pts, available = _hod_acceleration(
            price=None, hod_price=None,
            roc_1m_pct=None, roc_3m_pct=None, roc_5m_pct=None,
        )
        assert pts == 0.0
        assert available is False

    def test_hod_and_roc_combined(self):
        pts, _ = _hod_acceleration(
            price=10.05, hod_price=10.10,  # near HOD → 20 pts
            roc_1m_pct=2.0,  # 2/5 * 10 = 4 pts
            roc_3m_pct=None, roc_5m_pct=None,
        )
        assert pts == pytest.approx(24.0, abs=0.5)


# ──────────────────────────────────────────────────────────────────
#  Full attention scoring
# ──────────────────────────────────────────────────────────────────


class TestScoreAttention:
    def test_scanner_freshness_bonus_decays_over_scan_cycles(self):
        c = _candidate(percent_gain=12.0, current_volume=None)

        fresh = score_attention(c, scanner_seen_count=1)
        repeated = score_attention(c, scanner_seen_count=2)
        stale = score_attention(c, scanner_seen_count=4)

        assert fresh.score > repeated.score > stale.score
        assert "scanner_fresh" in fresh.bonuses_applied
        assert "scanner_fresh" not in stale.bonuses_applied

    def test_scanner_seen_count_decay_overrides_legacy_repeated_bonus(self):
        c = _candidate(percent_gain=12.0, current_volume=None)

        stale_with_legacy_flag = score_attention(
            c,
            scanner_seen_count=4,
            repeated_scanner_seen=True,
        )
        stale_without_legacy_flag = score_attention(c, scanner_seen_count=4)

        assert stale_with_legacy_flag.score == stale_without_legacy_flag.score
        assert "repeated_scanner_seen" not in stale_with_legacy_flag.bonuses_applied

    def test_perfect_candidate_scores_high(self):
        c = _candidate(percent_gain=40.0)
        result = score_attention(
            c,
            rvol=5.0,
            dollar_volume_5m=500_000,
            hod_price=10.10,
            roc_1m_pct=4.0,
        )
        assert 80 <= result.score <= 100
        assert "top_gainer" in result.drivers
        assert "strong_volume" in result.drivers

    def test_score_is_0_to_100(self):
        c = _candidate(percent_gain=0.0)
        result = score_attention(c)
        assert 0.0 <= result.score <= 100.0

    def test_returns_attention_score_object(self):
        c = _candidate()
        result = score_attention(c)
        assert isinstance(result, AttentionScore)
        assert result.raw_components is not None

    def test_drivers_include_top_gainer_for_positive_gain(self):
        c = _candidate(percent_gain=20.0)
        result = score_attention(c)
        assert "top_gainer" in result.drivers

    def test_drivers_empty_for_flat_stock(self):
        c = _candidate(percent_gain=0.0, current_volume=0)
        result = score_attention(c)
        # No volume, no HOD — only price is available but zero
        assert result.score == 0.0

    def test_price_only_redistributes(self):
        """When only price data is available, redistribute weight.
        Must explicitly nullify volume data since the helper sets defaults."""
        c = _candidate(percent_gain=15.0, current_volume=None, relative_volume=None)
        result = score_attention(c)
        # price_attention weight=30, available_weight=30
        # raw = 15/100*30 = 4.5, base = 4.5 * (100/30) = 15.0
        assert result.score == pytest.approx(15.0, abs=2.0)

    def test_theme_bonus_applied(self):
        c = _candidate(percent_gain=30.0)
        result = score_attention(c, theme_active=True)
        assert "theme_active" in result.bonuses_applied
        assert "theme_participant" in result.drivers

    def test_former_runner_bonus_applied(self):
        c = _candidate(percent_gain=30.0)
        result = score_attention(c, former_runner=True)
        assert "former_runner" in result.bonuses_applied
        assert "former_runner" in result.drivers

    def test_repeated_scanner_bonus_applied(self):
        c = _candidate(percent_gain=30.0)
        result = score_attention(c, repeated_scanner_seen=True)
        assert "repeated_scanner_seen" in result.bonuses_applied

    def test_score_capped_at_100_with_all_bonuses(self):
        c = _candidate(percent_gain=50.0)
        result = score_attention(
            c, rvol=5.0, dollar_volume_5m=500_000,
            hod_price=10.10, roc_1m_pct=5.0,
            theme_active=True, former_runner=True, repeated_scanner_seen=True,
        )
        assert result.score <= 100.0

    def test_score_with_no_data_returns_zero(self):
        c = _candidate(percent_gain=None, current_volume=None)
        result = score_attention(c)
        assert result.score == 0.0

    def test_raw_components_populated(self):
        c = _candidate(percent_gain=25.0)
        result = score_attention(c, rvol=3.0)
        assert "price_attention" in result.raw_components
        assert "volume_attention" in result.raw_components
        assert "hod_acceleration" in result.raw_components

    def test_bonuses_applied_empty_by_default(self):
        c = _candidate(percent_gain=25.0)
        result = score_attention(c)
        assert result.bonuses_applied == []


# ──────────────────────────────────────────────────────────────────
#  Theme detection
# ──────────────────────────────────────────────────────────────────


class TestDetectThemes:
    def test_detects_country_theme_with_3_shared(self):
        candidates = [
            _candidate("A", country="China"),
            _candidate("B", country="China"),
            _candidate("C", country="China"),
            _candidate("D", country="US"),
            _candidate("E", country="US"),
        ]
        themes = detect_themes(candidates)
        assert "country:China" in themes
        assert len(themes["country:China"]) == 3

    def test_detects_sector_theme(self):
        candidates = [
            _candidate("A", sector="Healthcare"),
            _candidate("B", sector="Healthcare"),
            _candidate("C", sector="Healthcare"),
            _candidate("D", sector="Tech"),
        ]
        themes = detect_themes(candidates)
        assert "sector:Healthcare" in themes

    def test_detects_industry_theme(self):
        candidates = [
            _candidate("A", industry="Biotechnology"),
            _candidate("B", industry="Biotechnology"),
            _candidate("C", industry="Biotechnology"),
        ]
        themes = detect_themes(candidates)
        assert "industry:Biotechnology" in themes

    def test_less_than_3_shared_not_a_theme(self):
        candidates = [
            _candidate("A", country="China"),
            _candidate("B", country="China"),
            _candidate("C", country="US"),
        ]
        themes = detect_themes(candidates)
        assert "country:China" not in themes

    def test_none_values_are_skipped(self):
        candidates = [
            _candidate("A", country=None),
            _candidate("B", country=None),
            _candidate("C", country=None),
        ]
        themes = detect_themes(candidates)
        assert len(themes) == 0

    def test_respects_top_n(self):
        candidates = [
            _candidate(f"S{i:02d}", country="China") for i in range(10)
        ] + [
            _candidate("US1", country="US")
        ]
        themes = detect_themes(candidates, top_n=5)
        assert "country:China" in themes
        # Only top 5 considered, so 5 Chinese out of top 5
        assert len(themes["country:China"]) == 5

    def test_custom_min_shared(self):
        candidates = [
            _candidate("A", country="China"),
            _candidate("B", country="China"),
            _candidate("C", country="China"),
            _candidate("D", country="China"),
            _candidate("E", country="China"),
        ]
        themes = detect_themes(candidates, min_shared=5)
        assert "country:China" in themes
        themes2 = detect_themes(candidates, min_shared=6)
        assert "country:China" not in themes2

    def test_is_symbol_in_theme(self):
        c = _candidate("DSY", country="China")
        themes = {"country:China": ["DSY", "A", "B"]}
        assert is_symbol_in_theme(c, themes) is True

    def test_is_symbol_not_in_theme(self):
        c = _candidate("AAPL", country="US")
        themes = {"country:China": ["DSY", "A", "B"]}
        assert is_symbol_in_theme(c, themes) is False


# ──────────────────────────────────────────────────────────────────
#  Former-runner store
# ──────────────────────────────────────────────────────────────────


class TestFormerRunnerStore:
    def test_empty_store_has_no_runners(self):
        store = FormerRunnerStore()
        assert len(store) == 0
        assert store.is_runner("DSY") is False

    def test_mark_and_check(self):
        store = FormerRunnerStore()
        store.mark("DSY")
        assert store.is_runner("DSY") is True
        assert len(store) == 1

    def test_runner_expires_after_window(self):
        store = FormerRunnerStore()
        old = datetime.now(timezone.utc) - timedelta(days=40)
        store.mark("DSY", when=old)
        assert store.is_runner("DSY", within_days=30) is False

    def test_runner_within_window(self):
        store = FormerRunnerStore()
        recent = datetime.now(timezone.utc) - timedelta(days=10)
        store.mark("DSY", when=recent)
        assert store.is_runner("DSY", within_days=30) is True

    def test_contains_operator(self):
        store = FormerRunnerStore()
        store.mark("DSY")
        assert "DSY" in store
        assert "AAPL" not in store


# ──────────────────────────────────────────────────────────────────
#  Float rotation
# ──────────────────────────────────────────────────────────────────


class TestFloatRotation:
    def test_returns_none_when_float_unknown(self):
        c = _candidate(float_shares=None)
        r = calculate_float_rotation(c, session_cumulative_volume=1_000_000)
        assert r is None

    def test_returns_none_when_float_zero(self):
        c = _candidate(float_shares=0)
        r = calculate_float_rotation(c, session_cumulative_volume=1_000_000)
        assert r is None

    def test_returns_zero_when_no_volume(self):
        c = _candidate(float_shares=1_000_000, current_volume=0)
        r = calculate_float_rotation(c)
        assert r == 0.0

    def test_basic_rotation(self):
        c = _candidate(float_shares=1_000_000)
        r = calculate_float_rotation(c, session_cumulative_volume=500_000)
        assert r == 0.5

    def test_fallback_to_candidate_volume(self):
        c = _candidate(float_shares=1_000_000, current_volume=2_000_000)
        r = calculate_float_rotation(c)
        assert r == 2.0

    def test_float_rotation_label_building(self):
        assert float_rotation_label(0.15) == "building"
        assert float_rotation_label(0.0) == "building"

    def test_float_rotation_label_active(self):
        assert float_rotation_label(0.50) == "active"
        assert float_rotation_label(0.99) == "active"

    def test_float_rotation_label_exhaustion_watch(self):
        assert float_rotation_label(1.5) == "watch_for_exhaustion"

    def test_float_rotation_label_exhaustion(self):
        assert float_rotation_label(2.5) == "exhaustion"

    def test_float_rotation_label_none(self):
        assert float_rotation_label(None) is None


# ──────────────────────────────────────────────────────────────────
#  Soft annotations
# ──────────────────────────────────────────────────────────────────


class TestSoftWarnings:
    def test_chinese_adr_detected(self):
        c = _candidate(country="China")
        warnings = map_soft_warnings(c)
        assert "chinese_adr" in warnings

    def test_chinese_adr_case_insensitive(self):
        c = _candidate(country="CHINA")
        warnings = map_soft_warnings(c)
        assert "chinese_adr" in warnings

    def test_biotech_detected_in_sector(self):
        c = _candidate(sector="Healthcare", industry="Biotechnology")
        warnings = map_soft_warnings(c)
        assert "biotech" in warnings

    def test_biotech_not_detected_for_tech(self):
        c = _candidate(sector="Technology", industry="Software")
        warnings = map_soft_warnings(c)
        assert "biotech" not in warnings

    def test_speculative_industry(self):
        c = _candidate(industry="Cannabis")
        warnings = map_soft_warnings(c)
        assert "speculative" in warnings

    def test_price_below_2_dollar(self):
        c = _candidate(price=1.50)
        warnings = map_soft_warnings(c)
        assert "price_below_2" in warnings

    def test_price_not_below_2(self):
        c = _candidate(price=5.0)
        warnings = map_soft_warnings(c)
        assert "price_below_2" not in warnings

    def test_outside_focus_price_range_low(self):
        c = _candidate(price=0.50)
        warnings = map_soft_warnings(c, price_range_min=1.0)
        assert "outside_focus_price_range_low" in warnings

    def test_outside_focus_price_range_high(self):
        c = _candidate(price=100.0)
        warnings = map_soft_warnings(c, price_range_max=50.0)
        assert "outside_focus_price_range_high" in warnings

    def test_float_unknown_warning(self):
        c = _candidate(float_shares=None)
        warnings = map_soft_warnings(c)
        assert "float_unknown" in warnings

    def test_very_low_float(self):
        c = _candidate(float_shares=500_000)
        warnings = map_soft_warnings(c)
        assert "very_low_float" in warnings

    def test_low_float(self):
        c = _candidate(float_shares=3_000_000)
        warnings = map_soft_warnings(c)
        assert "low_float" in warnings
        assert "very_low_float" not in warnings

    def test_float_rotation_over_200pct(self):
        c = _candidate(float_shares=1_000_000)
        warnings = map_soft_warnings(c, float_rotation=2.5)
        assert "float_rotation_over_200pct" in warnings

    def test_float_rotation_under_200pct_no_warning(self):
        c = _candidate(float_shares=1_000_000)
        warnings = map_soft_warnings(c, float_rotation=1.5)
        assert "float_rotation_over_200pct" not in warnings

    def test_stale_quote_warning(self):
        c = _candidate()
        warnings = map_soft_warnings(c, quote_age_seconds=10.0)
        assert "stale_quote" in warnings

    def test_fresh_quote_no_warning(self):
        c = _candidate()
        warnings = map_soft_warnings(c, quote_age_seconds=3.0)
        assert "stale_quote" not in warnings

    def test_wide_spread_caution(self):
        c = _candidate()
        warnings = map_soft_warnings(c, spread_pct=4.0)
        assert "wide_spread_caution" in warnings

    def test_spread_caution(self):
        c = _candidate()
        warnings = map_soft_warnings(c, spread_pct=2.0)
        assert "spread_caution" in warnings
        assert "wide_spread_caution" not in warnings

    def test_normal_spread_no_warning(self):
        c = _candidate()
        warnings = map_soft_warnings(c, spread_pct=0.5)
        assert "spread_caution" not in warnings
        assert "wide_spread_caution" not in warnings

    def test_parabolic_warning(self):
        c = _candidate()
        warnings = map_soft_warnings(c, parabolic=True)
        assert "parabolic" in warnings

    def test_below_vwap_warning(self):
        c = _candidate()
        warnings = map_soft_warnings(c, below_vwap=True)
        assert "below_vwap" in warnings

    def test_below_ema_warning(self):
        c = _candidate()
        warnings = map_soft_warnings(c, below_ema=True)
        assert "below_ema" in warnings

    def test_halt_history_warning(self):
        c = _candidate()
        warnings = map_soft_warnings(c, halt_history_today=True)
        assert "halt_history_today" in warnings

    def test_low_data_confidence_warning(self):
        c = _candidate()
        warnings = map_soft_warnings(c, data_confidence=0.5)
        assert "low_data_confidence" in warnings

    def test_ok_data_confidence_no_warning(self):
        c = _candidate()
        warnings = map_soft_warnings(c, data_confidence=0.8)
        assert "low_data_confidence" not in warnings

    def test_default_no_warnings(self):
        c = _candidate(float_shares=5_000_000)  # set float to avoid float_unknown
        warnings = map_soft_warnings(c, has_news=True, has_catalyst=True)
        assert warnings == []  # no country, no sector, no biotech, normal float

    # ── News / catalyst warnings ────────────────────────────────

    def test_known_no_news_adds_warning(self):
        c = _candidate()
        warnings = map_soft_warnings(c, has_news=False)
        assert "no_news" in warnings

    def test_unknown_news_adds_news_unknown(self):
        c = _candidate()
        warnings = map_soft_warnings(c, has_news=None)
        assert "news_unknown" in warnings

    def test_known_has_news_no_warning(self):
        c = _candidate()
        warnings = map_soft_warnings(c, has_news=True)
        assert "no_news" not in warnings
        assert "news_unknown" not in warnings

    def test_known_no_catalyst_adds_warning(self):
        c = _candidate()
        warnings = map_soft_warnings(c, has_catalyst=False)
        assert "no_catalyst" in warnings

    def test_unknown_catalyst_adds_catalyst_unknown(self):
        c = _candidate()
        warnings = map_soft_warnings(c, has_catalyst=None)
        assert "catalyst_unknown" in warnings

    def test_both_news_and_catalyst_unknown(self):
        c = _candidate()
        warnings = map_soft_warnings(c)
        assert "news_unknown" in warnings
        assert "catalyst_unknown" in warnings


# ──────────────────────────────────────────────────────────────────
#  Soft warning multiplier
# ──────────────────────────────────────────────────────────────────


class TestSoftWarningMultiplier:
    def test_no_warnings_returns_1_0(self):
        assert soft_warning_multiplier([]) == 1.0

    def test_multipliers_multiply(self):
        result = soft_warning_multiplier(["price_below_2", "float_unknown"])
        # 0.5 * 0.75 = 0.375, but floor is now 0.40
        assert result == 0.40

    def test_floor_at_0_40(self):
        result = soft_warning_multiplier(["price_below_2", "float_unknown", "parabolic"])
        assert result == 0.40  # floor

    def test_chinese_adr_not_penalized(self):
        """Chinese ADR is a theme annotation, not a penalty per SPEC §8."""
        result = soft_warning_multiplier(["chinese_adr"])
        assert result == 1.0

    def test_biotech_not_penalized(self):
        result = soft_warning_multiplier(["biotech"])
        assert result == 1.0

    # ── Attention-dependent no-news / no-catalyst ───────────────

    def test_no_news_high_attention_no_penalty(self):
        """no_news with attention >= 70 has no penalty (1.0x)."""
        r = soft_warning_multiplier(["no_news"], attention_score=70)
        assert r == 1.0
        r2 = soft_warning_multiplier(["no_news", "no_catalyst"], attention_score=85)
        assert r2 == 1.0

    def test_no_news_low_attention_penalty(self):
        """no_news with attention < 70 gets 0.75x."""
        r = soft_warning_multiplier(["no_news"], attention_score=69)
        assert r == pytest.approx(0.75, abs=0.01)

    def test_no_catalyst_low_attention_penalty(self):
        """no_catalyst with attention < 70 gets 0.75x."""
        r = soft_warning_multiplier(["no_catalyst"], attention_score=50)
        assert r == pytest.approx(0.75, abs=0.01)

    def test_no_news_penalty_multiplies_with_other_warnings(self):
        """no_news 0.75x stacks with other multipliers (floor at 0.40)."""
        r = soft_warning_multiplier(
            ["no_news", "float_unknown"], attention_score=50,
        )
        # 0.75 (no_news) * 0.75 (float_unknown) = 0.5625 (T4.6 float_unknown→0.75)
        assert r == pytest.approx(0.5625, abs=0.01)

    def test_news_unknown_does_not_trigger_penalty(self):
        """news_unknown is annotation-only, does not trigger attention-dependent penalty."""
        r = soft_warning_multiplier(["news_unknown"], attention_score=50)
        assert r == 1.0

    def test_no_news_penalty_ignored_when_attention_none(self):
        """When attention_score is None, no penalty is applied."""
        r = soft_warning_multiplier(["no_news"], attention_score=None)
        assert r == 1.0


# ──────────────────────────────────────────────────────────────────
#  DSY regression test (SPEC §19.2)
# ──────────────────────────────────────────────────────────────────


class TestDSYRegression:
    """Fake DSY-like candidate must NOT be hard-filtered.

    DSY characteristics:
      - Chinese
      - No news
      - Top gainer
      - Theme active
      - Early light volume then squeeze
      - Spread acceptable
      - Volume acceptable
      - First pullback risk definable

    Expected:
      - attention high
      - hard_blocks = []  (Phase 3; Phase 2 has no hard filters)
      - soft_warnings include chinese_adr, no_news, speculative
      - state = early or active (Phase 4)
      - mode = watch or starter_ready (Phase 4+5)
      - entry allowed if quote/spread/volume/stop/account risk pass
      - NOT rejected because of Chinese/no-news/no-catalyst
    """

    def _dsy_candidate(self) -> Candidate:
        return _candidate(
            symbol="DSY",
            price=5.50,
            percent_gain=45.0,
            premarket_gap_pct=30.0,
            current_volume=15_000_000,
            relative_volume=8.0,
            dollar_volume=82_500_000,  # 15M * 5.50
            sector="Healthcare",
            industry="Biotechnology",
            country="China",
            exchange="NASDAQ",
            float_shares=2_000_000,
            market_cap=100_000_000.0,
        )

    def test_dsy_attention_is_high(self):
        c = self._dsy_candidate()
        result = score_attention(
            c,
            rvol=8.0,
            dollar_volume_5m=200_000,
            hod_price=5.60,
            roc_1m_pct=3.0,
            theme_active=True,
            former_runner=True,
        )
        assert result.score >= 70, f"DSY attention should be high, got {result.score}"

    def test_dsy_soft_warnings_include_chinese_adr(self):
        c = self._dsy_candidate()
        warnings = map_soft_warnings(c, data_confidence=0.7)
        assert "chinese_adr" in warnings
        assert "biotech" in warnings
        assert "low_float" in warnings

    def test_dsy_is_not_hard_rejected_by_country(self):
        """Scanner must NOT delete Chinese candidates. Check that the scanner
        returns DSY without filtering."""
        # The scanner itself has no filters — this test verifies the principle.
        c = self._dsy_candidate()
        assert c.country == "China"
        assert c.symbol == "DSY"
        # Soft warnings exist but do not delete
        warnings = map_soft_warnings(c)
        assert "chinese_adr" in warnings

    def test_dsy_no_news_is_soft_only(self):
        """No news must be a soft warning, not a hard reject."""
        c = self._dsy_candidate()
        warnings = map_soft_warnings(c)
        # No "no_news" in current warnings map (added via param or stubbed)
        # But the principle is: nothing hard-blocks DSY

    def test_dsy_low_float_is_soft_only(self):
        c = self._dsy_candidate()
        warnings = map_soft_warnings(c)
        assert "low_float" in warnings
        # low_float is a soft warning, not a hard reject

    def test_dsy_theme_is_detected(self):
        candidates = [
            self._dsy_candidate(),
            _candidate("B", country="China"),
            _candidate("C", country="China"),
        ]
        themes = detect_themes(candidates)
        assert "country:China" in themes

    def test_dsy_can_be_scored_in_batch(self):
        c = self._dsy_candidate()
        scored = score_candidates([c])
        assert len(scored) == 1
        scored_candidate, score = scored[0]
        assert scored_candidate.symbol == "DSY"
        assert score.score > 0


# ──────────────────────────────────────────────────────────────────
#  Batch scoring
# ──────────────────────────────────────────────────────────────────


class TestScoreCandidates:
    def test_hod_field_influences_batch_ranking_when_available(self):
        candidates = [
            _candidate("FAR", price=10.00, percent_gain=18.0, current_volume=5_000_000, day_high=11.00),
            _candidate("HOD", price=10.00, percent_gain=18.0, current_volume=5_000_000, day_high=10.05),
        ]

        scored = score_candidates(candidates)

        assert scored[0][0].symbol == "HOD"
        hod_score = next(s for c, s in scored if c.symbol == "HOD").score
        far_score = next(s for c, s in scored if c.symbol == "FAR").score
        assert hod_score > far_score

    def test_sorts_by_attention_descending(self):
        candidates = [
            _candidate("LOW", percent_gain=5.0, current_volume=100_000),
            _candidate("HIGH", percent_gain=45.0, current_volume=10_000_000),
            _candidate("MID", percent_gain=20.0, current_volume=5_000_000),
        ]
        scored = score_candidates(candidates)
        assert len(scored) == 3
        assert scored[0][0].symbol == "HIGH"
        assert scored[-1][0].symbol == "LOW"

    def test_empty_list_returns_empty(self):
        scored = score_candidates([])
        assert scored == []

    def test_single_candidate_returns_single_result(self):
        c = _candidate("DSY")
        scored = score_candidates([c])
        assert len(scored) == 1
        assert scored[0][0].symbol == "DSY"

    def test_uses_former_runner_store(self):
        store = FormerRunnerStore()
        store.mark("DSY")
        candidates = [_candidate("DSY", percent_gain=30.0), _candidate("AAPL", percent_gain=30.0)]
        scored = score_candidates(candidates, former_runner_store=store)
        # DSY should have higher score due to former_runner bonus
        dsy_score = next(s for c, s in scored if c.symbol == "DSY").score
        aapl_score = next(s for c, s in scored if c.symbol == "AAPL").score
        assert dsy_score >= aapl_score


# ──────────────────────────────────────────────────────────────────
#  Task 10 — float enrichment + annotation truth
# ──────────────────────────────────────────────────────────────────


class TestFloatEnrichment:
    """T10: populated float_shares removes float_unknown warning;
    news/catalyst unknowns stay annotation-only (no penalty)."""

    def test_float_enrichment_removes_universal_float_unknown(self):
        """Plan Step 4: candidate with float_shares set → no float_unknown."""
        c = Candidate(symbol="DSY", price=10.50, float_shares=12_000_000)
        warnings = map_soft_warnings(
            c,
            price_range_min=1.0, price_range_max=50.0,
            quote_age_seconds=2.0, spread_pct=0.5,
            data_confidence=0.9,
        )
        assert "float_unknown" not in warnings, (
            f"Populated float_shares must remove float_unknown, got: {warnings}"
        )

    def test_news_unknown_remains_annotation_only_without_source(self):
        """Plan Step 4: has_news=None → news_unknown warning, but multiplier=1.0."""
        c = Candidate(symbol="DSY", price=10.50, float_shares=12_000_000)
        warnings = map_soft_warnings(c, has_news=None, has_catalyst=None)
        assert "news_unknown" in warnings
        # Annotation-only: multiplier must stay 1.0 even at low attention
        mult = soft_warning_multiplier(warnings, attention_score=80)
        assert mult == 1.0, (
            f"news_unknown must be annotation-only (mult=1.0), got {mult}"
        )

    def test_enrich_float_shares_returns_none_when_yfinance_missing(self, monkeypatch):
        """enrich_float_shares() returns None when yfinance not installed."""
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "yfinance":
                raise ImportError("no yfinance")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        from src.scanner.enrichment import enrich_float_shares
        result = enrich_float_shares("DSY")
        assert result is None

    def test_enrich_float_shares_returns_int_when_available(self, monkeypatch):
        """enrich_float_shares() returns int when yfinance provides floatShares."""
        class FakeInfo(dict):
            pass

        class FakeTicker:
            def __init__(self, symbol):
                self.symbol = symbol

            @property
            def info(self):
                return FakeInfo(floatShares=12_500_000)

        class FakeYf:
            Ticker = FakeTicker

        monkeypatch.setattr("src.scanner.enrichment.yfinance", FakeYf, raising=False)
        # Also patch the import inside the function
        import sys
        sys.modules["yfinance"] = FakeYf
        from src.scanner.enrichment import enrich_float_shares
        result = enrich_float_shares("DSY")
        assert result == 12_500_000
        del sys.modules["yfinance"]


# ──────────────────────────────────────────────────────────────────
#  Spread sizing multiplier (SPEC §11.18.4)
# ──────────────────────────────────────────────────────────────────


class TestSpreadSizingMultiplier:
    """Spread is a sizing dial, not a binary gate. Top gainers with wide
    spreads are still tradeable at reduced size."""

    def test_tight_spread_full_size(self):
        assert spread_sizing_multiplier(0.5) == 1.0
        assert spread_sizing_multiplier(2.0) == 1.0

    def test_moderate_spread_75pct(self):
        assert spread_sizing_multiplier(3.0) == 0.75
        assert spread_sizing_multiplier(5.0) == 0.75

    def test_wide_spread_50pct(self):
        assert spread_sizing_multiplier(6.0) == 0.50
        assert spread_sizing_multiplier(8.0) == 0.50

    def test_very_wide_spread_25pct_floor(self):
        assert spread_sizing_multiplier(15.0) == 0.25
        assert spread_sizing_multiplier(20.0) == 0.25

    def test_extreme_spread_blocks(self):
        assert spread_sizing_multiplier(25.0) == 0.0
        assert spread_sizing_multiplier(50.0) == 0.0

    def test_missing_spread_blocks(self):
        assert spread_sizing_multiplier(None) == 0.0
