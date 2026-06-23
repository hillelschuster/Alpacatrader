"""
Phase 2 — Free-tier scanner adapter.

Produces ``Candidate`` objects from the Finviz free screener top-gainer
table. If Finviz is empty or clearly stale, falls back to the bounded
yfinance watchlist scanner.

Does NOT hard-filter Finviz candidates by price. Every name that appears
on the scanner is returned — soft annotations and hard checks happen in
later layers. The yfinance fallback remains bounded by its own curated
watchlist and price/gap heuristics.

Also provides a manual-watchlist fallback for emergency use when dynamic
scanners fail, per SPEC section 5.2 bullet 4.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from src.models.schemas import Candidate
from src.scanner.enrichment import (
    FinvizRow,
    enrich_float_shares,
    scrape_finviz_gainers,
    scrape_yfinance_gainers,
)


def scan_finviz_candidates(
    max_candidates: int = 30,
    *,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
) -> list[Candidate]:
    """Scan Finviz top gainers and return Phase 1 ``Candidate`` objects.

    Wraps the existing ``scrape_finviz_gainers()`` parser. If Finviz is
    empty or stale, automatically falls back to
    ``scrape_yfinance_gainers()``.

    No attention scoring here — pure discovery. Finviz rows are not hard
    filtered by price. The bounded yfinance fallback may use caller-supplied
    price bounds when present; otherwise it uses broad defaults.

    Parameters
    ----------
    max_candidates : int
        Maximum number of candidates to return (default 30).
    min_price : float or None
        Optional lower price bound.  Candidates below this are still returned
        but can be annotated later as `soft_warning = outside_focus_price_range`.
    max_price : float or None
        Optional upper price bound (same soft-annotation philosophy).

    Returns
    -------
    list[Candidate]
        Unsorted list of discovered candidates.  The caller is responsible for
        enrichment, attention scoring, and ranking.
    """
    rows: dict[str, FinvizRow] = scrape_finviz_gainers()
    source = "finviz"
    fallback_min = min_price if min_price is not None else 0.0
    fallback_max = max_price if max_price is not None else 10_000.0

    if not rows:
        logger.warning("Finviz scanner returned zero rows — trying yfinance fallback")
        rows = scrape_yfinance_gainers(min_price=fallback_min, max_price=fallback_max)
        if rows:
            source = "yfinance_fallback"
        else:
            return []

    # Detect stale/cached Finviz output (T5.7)
    from src.scanner.enrichment import _finviz_is_stale
    if source == "finviz" and _finviz_is_stale(rows):
        logger.warning("Finviz scanner returned stale data — trying yfinance fallback")
        rows = scrape_yfinance_gainers(min_price=fallback_min, max_price=fallback_max)
        if rows:
            source = "yfinance_fallback"
        else:
            return []

    now = datetime.now(timezone.utc)
    candidates: list[Candidate] = []

    for ticker, row in rows.items():
        price_val: Optional[float] = row.price if row.price > 0 else None

        candidate = Candidate(
            symbol=row.ticker,
            price=price_val,
            percent_gain=row.change_pct if row.change_pct != 0.0 else None,
            current_volume=row.volume if row.volume > 0 else None,
            sector=row.sector if row.sector else None,
            industry=row.industry if row.industry else None,
            country=row.country if row.country else None,
            exchange=row.exchange if row.exchange else None,
            market_cap=row.market_cap if row.market_cap > 0.0 else None,
            float_shares=enrich_float_shares(row.ticker),
            source=source,
            source_timestamp=now,
        )
        candidates.append(candidate)

    # Trim to max, preserving the order Finviz returned (sorted by % gain)
    return candidates[:max_candidates]


def scan_manual_watchlist(symbols: list[str]) -> list[Candidate]:
    """Fallback scanner: produce ``Candidate`` objects for a static watchlist.

    Per SPEC §5.2.4, this is allowed only when dynamic scanners fail.
    The caller must set ``source = "manual_emergency_watchlist"``.
    Candidates are bare — enrichment must confirm attention later.

    Parameters
    ----------
    symbols : list[str]
        Ticker symbols to watch.

    Returns
    -------
    list[Candidate]
        Bare candidates with only ``symbol`` and ``source`` populated.
    """
    now = datetime.now(timezone.utc)
    return [
        Candidate(
            symbol=s.strip().upper(),
            source="manual_emergency_watchlist",
            source_timestamp=now,
        )
        for s in symbols
        if s.strip()
    ]
