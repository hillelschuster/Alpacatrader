"""
Paper-mode market data enrichment helper.

Fetches real-time quote and bar data from Alpaca Markets for paper-mode
candidates. Returns a ``MarketSnapshot`` bundle or None when enrichment
is unavailable.

Usage::

    from src.market_data import build_market_snapshot
    snapshot = build_market_snapshot(candidate)
    if snapshot is not None:
        # use snapshot.bars, snapshot.quote_age_seconds, etc.

Spec: §22.15 items 17, 20 — paper mode must succeed through rebuild path
with real-time data enrichment.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from src.decision_pipeline import MarketSnapshot
from src.entries import Bar


# ── Helpers ────────────────────────────────────────────────────────────


def _compute_ema(values: list[float], period: int) -> Optional[float]:
    """Exponential moving average from a list of values (most recent last).

    Uses SMA seed for the first *period* values, then EMA for the rest.
    Returns None when fewer than *period* values are available.
    """
    if len(values) < period:
        return None
    k = 2.0 / (period + 1.0)
    ema = sum(values[:period]) / period  # SMA seed
    for v in values[period:]:
        ema = v * k + ema * (1.0 - k)
    return ema


# ── Main entry point ────────────────────────────────────────────────────


def build_market_snapshot(candidate) -> Optional[MarketSnapshot]:
    """Fetch real-time quote + bars for *candidate* via Alpaca.

    Returns a ``MarketSnapshot`` when Alpaca keys are configured and data
    is successfully retrieved.  Returns ``None`` when:

    - The ``alpaca-py`` package is not installed.
    - Alpaca API keys are not set in the environment.
    - The API call fails (network, rate limit, auth, etc.).

    When ``None`` is returned, the caller (paper-mode main.py) still runs
    the decision pipeline — hard filters mechanically block candidates
    that lack quote/spread/bars, so there is no silent skip.
    """
    # Lazy import to keep module import side-effect-free
    # (alpaca-py triggers websockets DeprecationWarning at import time)
    try:
        from alpaca.data.historical.stock import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestQuoteRequest, StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
    except ImportError:
        logger.info(
            "alpaca-py not installed — market data enrichment unavailable. "
            "Install with: pip install alpaca-py"
        )
        return None

    # Support both naming conventions; prefer ALPACA_* when both are set.
    api_key = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
    secret_key = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")

    if not api_key or not secret_key:
        logger.info(
            "Alpaca API keys not configured — market data enrichment unavailable. "
            "Set ALPACA_API_KEY / ALPACA_SECRET_KEY "
            "(or APCA_API_KEY_ID / APCA_API_SECRET_KEY) in .env"
        )
        return None

    try:
        client = StockHistoricalDataClient(api_key, secret_key)
        now = datetime.now(timezone.utc)

        # ── Latest quote ──────────────────────────────────────────
        quote_req = StockLatestQuoteRequest(symbol_or_symbols=candidate.symbol)
        quotes = client.get_stock_latest_quote(quote_req)
        alpaca_quote = quotes.get(candidate.symbol)

        quote_age_seconds: Optional[float] = None
        spread_pct: Optional[float] = None

        mid_price: Optional[float] = None
        if alpaca_quote is not None and alpaca_quote.timestamp is not None:
            age = (now - alpaca_quote.timestamp).total_seconds()
            quote_age_seconds = max(0.0, age)

            bid = alpaca_quote.bid_price
            ask = alpaca_quote.ask_price
            if bid is not None and ask is not None and bid > 0.0 and ask > 0.0 and ask >= bid:
                mid = (bid + ask) / 2.0
                if mid > 0.0:
                    spread_pct = (ask - bid) / mid * 100.0
                    mid_price = mid

        # Update candidate with Alpaca quote mid-price when available
        if mid_price is not None and mid_price > 0:
            candidate = candidate.model_copy(update={"price": mid_price})

        # ── Recent bars ───────────────────────────────────────────
        bars_req = StockBarsRequest(
            symbol_or_symbols=candidate.symbol,
            timeframe=TimeFrame.Minute,
            limit=20,
        )
        bar_set = client.get_stock_bars(bars_req)
        alpaca_bars = bar_set.data.get(candidate.symbol, [])

        bars: list[Bar] = []
        for ab in alpaca_bars:
            bars.append(Bar(
                open=ab.open,
                high=ab.high,
                low=ab.low,
                close=ab.close,
                volume=ab.volume,
                timestamp=ab.timestamp,
            ))

        # ── Derived enrichment ────────────────────────────────────
        vwap: Optional[float] = None
        ema9: Optional[float] = None
        day_high: Optional[float] = None
        prior_hod: Optional[float] = None
        dollar_volume_5m: Optional[float] = None

        if bars:
            close_prices = [b.close for b in bars]
            volumes = [b.volume for b in bars]

            # VWAP over all available bars
            total_pv = sum(c * v for c, v in zip(close_prices, volumes))
            total_v = sum(volumes)
            if total_v > 0:
                vwap = total_pv / total_v

            # EMA 9
            ema9 = _compute_ema(close_prices, 9)

            # Day high (from available bars — not the actual session high
            # without full history, but sufficient for paper estimation)
            day_high = max(b.high for b in bars)

            # Prior HOD: second-highest distinct high in the window
            distinct_highs = sorted({b.high for b in bars}, reverse=True)
            if len(distinct_highs) >= 2:
                prior_hod = distinct_highs[1]

            # Trailing 5-minute dollar volume
            last_5 = bars[-5:]
            if last_5:
                dollar_volume_5m = sum(b.close * b.volume for b in last_5)

        rvol = candidate.relative_volume

        return MarketSnapshot(
            candidate=candidate,
            bars=bars if bars else None,
            vwap=vwap,
            ema9=ema9,
            day_high=day_high,
            prior_hod=prior_hod,
            quote_age_seconds=quote_age_seconds,
            spread_pct=spread_pct,
            rvol=rvol,
            dollar_volume_5m=dollar_volume_5m,
        )

    except Exception as exc:
        logger.warning(
            "Market-data enrichment failed for {}: {}",
            candidate.symbol,
            exc,
        )
        return None
