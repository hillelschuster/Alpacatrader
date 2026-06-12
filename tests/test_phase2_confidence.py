"""Phase 2 data-confidence tests per SPEC section 4.4.

No network calls, no broker dependencies.
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.models.schemas import Candidate
from src.scanner.confidence import calculate_data_confidence, compute_scanner_age_seconds


# ── Helpers ───────────────────────────────────────────────────────


def _candidate(
    symbol: str = "DSY",
    *,
    price: float | None = 10.0,
    source_timestamp: datetime | None = None,
    float_shares: int | None = None,
    market_cap: float | None = None,
    sector: str | None = None,
    industry: str | None = None,
    country: str | None = None,
    premarket_high: float | None = None,
    premarket_low: float | None = None,
    premarket_gap_pct: float | None = None,
) -> Candidate:
    return Candidate(
        symbol=symbol,
        price=price,
        source_timestamp=source_timestamp,
        float_shares=float_shares,
        market_cap=market_cap,
        sector=sector,
        industry=industry,
        country=country,
        premarket_high=premarket_high,
        premarket_low=premarket_low,
        premarket_gap_pct=premarket_gap_pct,
    )


# ──────────────────────────────────────────────────────────────────
#  calculate_data_confidence
# ──────────────────────────────────────────────────────────────────


class TestCalculateDataConfidence:
    def test_perfect_candidate_scores_1_0(self):
        c = _candidate(
            source_timestamp=datetime.now(timezone.utc),
            float_shares=10_000_000,
            market_cap=1_000_000_000.0,
            sector="Technology",
            industry="Software",
            country="US",
            premarket_high=10.5,
            premarket_low=9.8,
            premarket_gap_pct=5.0,
        )
        result = calculate_data_confidence(
            c,
            now=c.source_timestamp,
            bars_available=True,
            bars_timestamp=c.source_timestamp,
        )
        assert result == 1.0

    def test_missing_source_timestamp_penalty(self):
        """Isolate source_timestamp penalty: set bars OK, all fillable fields present."""
        now = datetime.now(timezone.utc)
        c = _candidate(
            source_timestamp=None,
            float_shares=10_000_000, market_cap=1_000_000_000.0,
            sector="Tech", industry="SW", country="US",
            premarket_high=10.5, premarket_low=9.8, premarket_gap_pct=5.0,
        )
        result = calculate_data_confidence(
            c, now=now, bars_available=True, bars_timestamp=now,
        )
        assert result == 0.90  # only -0.10 for missing source_timestamp

    def test_stale_scanner_penalty(self):
        stale = datetime.now(timezone.utc) - timedelta(minutes=30)
        c = _candidate(source_timestamp=stale)
        result = calculate_data_confidence(c, now=datetime.now(timezone.utc))
        assert result <= 0.80  # -0.20

    def test_scanner_under_20_minutes_no_penalty(self):
        fresh = datetime.now(timezone.utc) - timedelta(minutes=10)
        c = _candidate(source_timestamp=fresh, float_shares=10_000_000, market_cap=1_000_000_000.0,
                       sector="Tech", industry="SW", country="US",
                       premarket_high=10.5, premarket_low=9.8, premarket_gap_pct=5.0)
        result = calculate_data_confidence(
            c, now=datetime.now(timezone.utc),
            bars_available=True, bars_timestamp=datetime.now(timezone.utc),
        )
        assert result == 1.0  # no scanner penalty since <20 min, all fields present

    def test_bars_missing_penalty(self):
        c = _candidate(source_timestamp=datetime.now(timezone.utc), float_shares=10_000_000,
                       market_cap=1_000_000_000.0, sector="Tech", industry="SW", country="US",
                       premarket_high=10.5, premarket_low=9.8, premarket_gap_pct=5.0)
        result = calculate_data_confidence(c, bars_available=False)
        assert result <= 0.80  # -0.20

    def test_bars_none_timestamp_penalty(self):
        c = _candidate(source_timestamp=datetime.now(timezone.utc), float_shares=10_000_000,
                       market_cap=1_000_000_000.0, sector="Tech", industry="SW", country="US",
                       premarket_high=10.5, premarket_low=9.8, premarket_gap_pct=5.0)
        result = calculate_data_confidence(c, bars_available=True, bars_timestamp=None)
        assert result <= 0.80

    def test_stale_bars_penalty(self):
        stale_bar = datetime.now(timezone.utc) - timedelta(minutes=10)
        c = _candidate(source_timestamp=datetime.now(timezone.utc), float_shares=10_000_000,
                       market_cap=1_000_000_000.0, sector="Tech", industry="SW", country="US",
                       premarket_high=10.5, premarket_low=9.8, premarket_gap_pct=5.0)
        result = calculate_data_confidence(c, bars_available=True, bars_timestamp=stale_bar, max_bar_age_seconds=60)
        assert result <= 0.80  # -0.20 for stale bars

    def test_missing_metadata_penalties(self):
        """Each missing meta field reduces confidence by 0.05."""
        c = _candidate(source_timestamp=datetime.now(timezone.utc))
        result = calculate_data_confidence(
            c, now=c.source_timestamp,
            bars_available=True, bars_timestamp=c.source_timestamp,
        )
        # 5 meta fields missing + 3 premarket missing = 8 × 0.05 = 0.40 penalty
        assert result >= 0.55  # 1.0 - 0.40 - floor at 0.3 → 0.60
        assert result <= 0.65  # exact is 0.60

    def test_missing_premarket_penalties(self):
        """Each missing premarket field reduces confidence by 0.05."""
        c = _candidate(
            source_timestamp=datetime.now(timezone.utc),
            float_shares=10_000_000, market_cap=1_000_000_000.0,
            sector="Tech", industry="SW", country="US",
            # all 3 premarket fields missing
        )
        result = calculate_data_confidence(
            c, now=c.source_timestamp,
            bars_available=True, bars_timestamp=c.source_timestamp,
        )
        assert result >= 0.80  # 1.0 - 0.15 = 0.85
        assert result <= 0.90

    def test_floor_at_0_3_when_price_present(self):
        """Even with massive penalties, floor at 0.3 if price exists."""
        c = _candidate(price=10.0)  # almost everything missing
        result = calculate_data_confidence(c, bars_available=False)
        assert result == 0.3

    def test_no_floor_when_price_missing(self):
        """Without critical price data, no 0.3 floor applies.
        Confidence drops naturally through all penalties."""
        c = _candidate(price=None)  # no critical data
        result = calculate_data_confidence(c, bars_available=False)
        # With max penalties and no floor, result hits the natural minimum
        assert result <= 0.3  # no floor protection

    def test_result_is_rounded_to_two_decimals(self):
        c = _candidate(source_timestamp=datetime.now(timezone.utc))
        result = calculate_data_confidence(c, bars_available=True, bars_timestamp=datetime.now(timezone.utc))
        # Verify result has at most 2 decimal places
        assert result == round(result, 2)
        formatted = str(result)
        if "." in formatted:
            decimals = len(formatted.split(".")[1])
            assert decimals <= 2

    def test_result_never_exceeds_1_0(self):
        c = _candidate(source_timestamp=datetime.now(timezone.utc), float_shares=1, market_cap=1,
                       sector="a", industry="b", country="c",
                       premarket_high=1, premarket_low=1, premarket_gap_pct=1)
        result = calculate_data_confidence(
            c, now=c.source_timestamp,
            bars_available=True, bars_timestamp=c.source_timestamp,
        )
        assert result <= 1.0

    def test_result_never_below_0_0(self):
        c = _candidate(price=None)
        result = calculate_data_confidence(c, bars_available=False)
        assert result >= 0.0

    def test_full_chain_confidence(self):
        """Test a realistic Phase-2 candidate: scanner data only, no bars."""
        c = _candidate(
            source_timestamp=datetime.now(timezone.utc) - timedelta(minutes=5),
            float_shares=None,  # missing
            market_cap=500_000_000.0,
            sector="Healthcare",
            industry="Biotechnology",
            country="China",
            premarket_high=None, premarket_low=None, premarket_gap_pct=None,  # all missing
        )
        result = calculate_data_confidence(c, bars_available=False)
        # Base 1.0
        # bars missing: -0.20
        # float missing: -0.05
        # 3 premarket missing: -0.15
        # = 0.60, floor at 0.3 (price exists) → keep 0.60
        assert result == 0.60


# ──────────────────────────────────────────────────────────────────
#  compute_scanner_age_seconds
# ──────────────────────────────────────────────────────────────────


class TestComputeScannerAgeSeconds:
    def test_returns_none_when_no_timestamp(self):
        c = _candidate(source_timestamp=None)
        age = compute_scanner_age_seconds(c)
        assert age is None

    def test_returns_age_in_seconds(self):
        now = datetime.now(timezone.utc)
        ts = now - timedelta(seconds=30)
        c = _candidate(source_timestamp=ts)
        age = compute_scanner_age_seconds(c, now=now)
        assert age == 30.0

    def test_returns_zero_for_now(self):
        now = datetime.now(timezone.utc)
        c = _candidate(source_timestamp=now)
        age = compute_scanner_age_seconds(c, now=now)
        assert age == 0.0

    def test_defaults_to_utcnow(self):
        """Without explicit ``now``, uses current UTC time."""
        c = _candidate(source_timestamp=datetime.now(timezone.utc))
        age = compute_scanner_age_seconds(c)
        assert age is not None
        assert age >= 0.0
