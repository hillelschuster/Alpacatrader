"""Phase 2 scanner adapter tests — no network calls, no broker dependencies.

Tests the Finviz scanner adapter and manual-watchlist fallback.
"""

from datetime import datetime, timezone
from typing import cast
from unittest.mock import patch

import pytest

from src.models.schemas import Candidate
from src.scanner.enrichment import FinvizRow
from src.scanner.scanner import scan_finviz_candidates, scan_manual_watchlist


# ── Helpers ───────────────────────────────────────────────────────


def _make_row(
    ticker: str = "DSY",
    price: float = 10.0,
    change_pct: float = 15.0,
    volume: int = 5_000_000,
    sector: str = "",
    industry: str = "",
    country: str = "",
    exchange: str = "",
    market_cap: float = 0.0,
) -> FinvizRow:
    return FinvizRow(
        ticker=ticker,
        company="Test Company",
        sector=sector,
        industry=industry,
        country=country,
        market_cap=market_cap,
        price=price,
        change_pct=change_pct,
        volume=volume,
        exchange=exchange,
    )


# ──────────────────────────────────────────────────────────────────
#  scan_finviz_candidates
# ──────────────────────────────────────────────────────────────────


class TestScanFinvizCandidates:
    @patch("src.scanner.scanner.scrape_finviz_gainers")
    def test_returns_candidates_for_valid_rows(self, mock_scrape):
        mock_scrape.return_value = {
            "DSY": _make_row("DSY", 10.0, 15.0, 5_000_000, sector="Technology", country="China"),
            "AAPL": _make_row("AAPL", 195.0, 3.0, 50_000_000),
        }

        result = scan_finviz_candidates()
        assert len(result) == 2
        assert all(isinstance(c, Candidate) for c in result)

    @patch("src.scanner.scanner.scrape_finviz_gainers")
    def test_sets_source_to_finviz(self, mock_scrape):
        mock_scrape.return_value = {"DSY": _make_row("DSY")}
        result = scan_finviz_candidates()
        assert result[0].source == "finviz"

    @patch("src.scanner.scanner.scrape_finviz_gainers")
    def test_sets_source_timestamp(self, mock_scrape):
        before = datetime.now(timezone.utc)
        mock_scrape.return_value = {"DSY": _make_row("DSY")}
        result = scan_finviz_candidates()
        after = datetime.now(timezone.utc)
        ts = cast(datetime, result[0].source_timestamp)
        assert before <= ts
        assert ts <= after

    @patch("src.scanner.scanner.scrape_finviz_gainers")
    def test_maps_price(self, mock_scrape):
        mock_scrape.return_value = {"DSY": _make_row("DSY", price=10.50)}
        c = scan_finviz_candidates()[0]
        assert c.price == 10.50

    @patch("src.scanner.scanner.scrape_finviz_gainers")
    def test_price_zero_becomes_none(self, mock_scrape):
        mock_scrape.return_value = {"DSY": _make_row("DSY", price=0.0)}
        c = scan_finviz_candidates()[0]
        assert c.price is None

    @patch("src.scanner.scanner.scrape_finviz_gainers")
    def test_maps_percent_gain(self, mock_scrape):
        mock_scrape.return_value = {"DSY": _make_row("DSY", change_pct=25.5)}
        c = scan_finviz_candidates()[0]
        assert c.percent_gain == 25.5

    @patch("src.scanner.scanner.scrape_finviz_gainers")
    def test_zero_change_becomes_none(self, mock_scrape):
        mock_scrape.return_value = {"DSY": _make_row("DSY", change_pct=0.0)}
        c = scan_finviz_candidates()[0]
        assert c.percent_gain is None

    @patch("src.scanner.scanner.scrape_finviz_gainers")
    def test_maps_volume(self, mock_scrape):
        mock_scrape.return_value = {"DSY": _make_row("DSY", volume=1_000_000)}
        c = scan_finviz_candidates()[0]
        assert c.current_volume == 1_000_000

    @patch("src.scanner.scanner.scrape_finviz_gainers")
    def test_zero_volume_becomes_none(self, mock_scrape):
        mock_scrape.return_value = {"DSY": _make_row("DSY", volume=0)}
        c = scan_finviz_candidates()[0]
        assert c.current_volume is None

    @patch("src.scanner.scanner.scrape_finviz_gainers")
    def test_maps_sector_industry_country_exchange(self, mock_scrape):
        mock_scrape.return_value = {
            "DSY": _make_row("DSY", sector="Healthcare", industry="Biotechnology", country="China", exchange="NASDAQ")
        }
        c = scan_finviz_candidates()[0]
        assert c.sector == "Healthcare"
        assert c.industry == "Biotechnology"
        assert c.country == "China"
        assert c.exchange == "NASDAQ"

    @patch("src.scanner.scanner.scrape_finviz_gainers")
    def test_maps_market_cap(self, mock_scrape):
        mock_scrape.return_value = {"DSY": _make_row("DSY", market_cap=500_000_000.0)}
        c = scan_finviz_candidates()[0]
        assert c.market_cap == 500_000_000.0

    @patch("src.scanner.scanner.scrape_finviz_gainers")
    def test_market_cap_zero_becomes_none(self, mock_scrape):
        mock_scrape.return_value = {"DSY": _make_row("DSY", market_cap=0.0)}
        c = scan_finviz_candidates()[0]
        assert c.market_cap is None

    @patch("src.scanner.scanner.scrape_finviz_gainers")
    def test_empty_string_fields_become_none(self, mock_scrape):
        mock_scrape.return_value = {
            "DSY": _make_row("DSY", sector="", industry="", country="", exchange="")
        }
        c = scan_finviz_candidates()[0]
        assert c.sector is None
        assert c.industry is None
        assert c.country is None
        assert c.exchange is None

    @patch("src.scanner.scanner.scrape_finviz_gainers")
    def test_empty_scraper_returns_empty_list(self, mock_scrape):
        mock_scrape.return_value = {}
        result = scan_finviz_candidates()
        assert result == []

    @patch("src.scanner.scanner.scrape_finviz_gainers")
    def test_respects_max_candidates(self, mock_scrape):
        rows = {f"S{i:02d}": _make_row(f"S{i:02d}", change_pct=float(50 - i)) for i in range(50)}
        mock_scrape.return_value = rows
        result = scan_finviz_candidates(max_candidates=25)
        assert len(result) == 25

    @patch("src.scanner.scanner.scrape_finviz_gainers")
    def test_does_not_hard_filter_by_price(self, mock_scrape):
        """Candidates outside focus price range are still returned (soft annotation later)."""
        mock_scrape.return_value = {"LOW": _make_row("LOW", price=0.50), "HIGH": _make_row("HIGH", price=500.0)}
        result = scan_finviz_candidates()
        assert len(result) == 2

    @patch("src.scanner.scanner.scrape_finviz_gainers")
    def test_does_not_filter_by_country(self, mock_scrape):
        """Chinese ADR, etc. must NOT be hard-filtered by the scanner."""
        mock_scrape.return_value = {"DSY": _make_row("DSY", country="China")}
        result = scan_finviz_candidates()
        assert len(result) == 1

    @patch("src.scanner.scanner.scrape_finviz_gainers")
    def test_does_not_filter_by_sector(self, mock_scrape):
        """Biotech, speculative sectors are NOT hard-filtered."""
        mock_scrape.return_value = {"BIO": _make_row("BIO", sector="Healthcare", industry="Biotechnology")}
        result = scan_finviz_candidates()
        assert len(result) == 1

    @patch("src.scanner.scanner.scrape_finviz_gainers")
    def test_candidate_is_frozen(self, mock_scrape):
        mock_scrape.return_value = {"DSY": _make_row("DSY")}
        c = scan_finviz_candidates()[0]
        with pytest.raises(ValueError):
            c.symbol = "CHANGED"


# ──────────────────────────────────────────────────────────────────
#  scan_manual_watchlist
# ──────────────────────────────────────────────────────────────────


class TestScanManualWatchlist:
    def test_returns_candidates_for_symbols(self):
        result = scan_manual_watchlist(["DSY", "AAPL", "TSLA"])
        assert len(result) == 3
        assert all(isinstance(c, Candidate) for c in result)

    def test_sets_source_to_manual_emergency(self):
        result = scan_manual_watchlist(["DSY"])
        assert result[0].source == "manual_emergency_watchlist"

    def test_sets_source_timestamp(self):
        before = datetime.now(timezone.utc)
        result = scan_manual_watchlist(["DSY"])
        after = datetime.now(timezone.utc)
        ts = result[0].source_timestamp
        assert before <= ts <= after

    def test_bare_candidates_have_no_enrichment(self):
        """Manual watchlist candidates start with only symbol + source — enrichment is separate."""
        result = scan_manual_watchlist(["DSY"])
        c = result[0]
        assert c.price is None
        assert c.percent_gain is None
        assert c.sector is None

    def test_strips_whitespace(self):
        result = scan_manual_watchlist([" DSY ", " AAPL "])
        assert result[0].symbol == "DSY"
        assert result[1].symbol == "AAPL"

    def test_uppercases(self):
        result = scan_manual_watchlist(["dsy"])
        assert result[0].symbol == "DSY"

    def test_empty_strings_are_skipped(self):
        result = scan_manual_watchlist(["DSY", "", "  ", "AAPL"])
        assert len(result) == 2

    def test_empty_list_returns_empty(self):
        result = scan_manual_watchlist([])
        assert result == []
