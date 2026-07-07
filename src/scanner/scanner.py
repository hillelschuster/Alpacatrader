"""
Phase 2 — Free-tier scanner adapter.

Produces ``Candidate`` objects from dynamic top-gainer sources. Static
watchlists are watch-only; they are not automatic trade discovery.

Does NOT hard-filter Finviz candidates by price. Every name that appears
on the scanner is returned — soft annotations and hard checks happen in
later layers.

Also provides a manual-watchlist fallback for emergency use when dynamic
scanners fail, per SPEC section 5.2 bullet 4.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger

from src.models.schemas import Candidate
from src.scanner.enrichment import (
    FinvizRow,
    enrich_float_shares,
    scrape_finviz_gainers,
)


def scan_alpaca_movers(
    max_candidates: int = 30,
    *,
    client: Any | None = None,
    api_key: str | None = None,
    secret_key: str | None = None,
) -> list[Candidate]:
    """Scan Alpaca market movers and return top-gainer candidates.

    Uses Alpaca's screener movers endpoint when credentials/client exist.
    Returns [] on missing SDK/keys/API failure so existing scanner fallback
    remains deterministic.
    """
    now = datetime.now(timezone.utc)

    if client is None:
        api_key = api_key or os.getenv("ALPACA_API_KEY")
        secret_key = secret_key or os.getenv("ALPACA_SECRET_KEY")
        if not api_key or not secret_key:
            return []
        try:
            from alpaca.data.historical.screener import ScreenerClient
        except ImportError:
            logger.warning("alpaca-py screener client unavailable — skipping Alpaca movers")
            return []
        client = ScreenerClient(api_key=api_key, secret_key=secret_key)

    try:
        from alpaca.data.requests import MarketMoversRequest
        movers = client.get_market_movers(MarketMoversRequest(top=max_candidates))
    except Exception as exc:
        logger.warning("Alpaca movers scanner failed — falling back: {}", exc)
        return []

    gainers = getattr(movers, "gainers", []) or []
    candidates: list[Candidate] = []
    for mover in gainers[:max_candidates]:
        symbol = str(getattr(mover, "symbol", "") or "").strip().upper()
        if not symbol:
            continue
        price = getattr(mover, "price", None)
        percent_change = getattr(mover, "percent_change", None)
        candidates.append(Candidate(
            symbol=symbol,
            price=float(price) if price is not None and float(price) > 0 else None,
            percent_gain=(
                float(percent_change)
                if percent_change is not None and float(percent_change) != 0.0
                else None
            ),
            source="alpaca_movers",
            source_timestamp=now,
        ))
    return candidates


def scan_dynamic_candidates(
    max_candidates: int = 30,
    *,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
) -> list[Candidate]:
    """Dynamic scanner chain: Alpaca movers first, then Finviz."""
    alpaca = scan_alpaca_movers(max_candidates=max_candidates)
    if alpaca:
        return alpaca[:max_candidates]
    return scan_finviz_candidates(
        max_candidates=max_candidates,
        min_price=min_price,
        max_price=max_price,
    )


def scan_finviz_candidates(
    max_candidates: int = 30,
    *,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
) -> list[Candidate]:
    """Scan Finviz top gainers and return Phase 1 ``Candidate`` objects.

    Wraps the existing ``scrape_finviz_gainers()`` parser. If Finviz is
    empty or stale, returns no trade-discovery candidates; the static
    yfinance watchlist stays watch-only.

    No attention scoring here — pure discovery. Finviz rows are not hard
    filtered by price.

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

    if not rows:
        logger.warning("Finviz scanner returned zero rows — static watchlist is watch-only")
        return []

    # Detect stale/cached Finviz output (T5.7)
    from src.scanner.enrichment import _finviz_is_stale
    if source == "finviz" and _finviz_is_stale(rows):
        logger.warning("Finviz scanner returned stale data — static watchlist is watch-only")
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
