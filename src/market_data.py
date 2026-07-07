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

Spec: paper mode must succeed through the pipeline with real-time data enrichment.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from src.decision_pipeline import MarketSnapshot
from src.entries import Bar

# ponytail: IEX ≈ 2.5% of US market volume. Scale to approximate total.
# Remove this scaling if SIP ($99/mo) is activated.
IEX_SCALE = 40.0

# Bar lookback — 60 bars = 1 hour of 1-min context for entry setup detection.
BAR_LIMIT = 60

# RVOL lookback — 20 trading days for average daily volume baseline.
RVOL_LOOKBACK_DAYS = 20

# ponytail: per-symbol daily cache for 20-day avg volume. TTL=1 trading day.
# Refreshed once per day per symbol. Avoids redundant API calls.
_avg_daily_volume_cache: dict[str, tuple[str, float]] = {}  # symbol → (date_str, avg_volume)


def fetch_avg_daily_volume(
    symbol: str,
    api_key: str,
    secret_key: str,
    lookback: int = RVOL_LOOKBACK_DAYS,
) -> Optional[float]:
    """Fetch 20-day average daily volume for RVOL computation.

    Cached per-symbol per-day. Returns None on any failure.
    """
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cached = _avg_daily_volume_cache.get(symbol)
    if cached and cached[0] == today:
        return cached[1]

    try:
        from alpaca.data.historical.stock import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        from alpaca.data.enums import DataFeed
    except ImportError:
        return None

    try:
        client = StockHistoricalDataClient(api_key, secret_key)
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame(amount=1, unit=TimeFrameUnit.Day),
            limit=lookback,
            feed=DataFeed.IEX,
        )
        bars = client.get_stock_bars(req)
        symbol_bars = bars.data.get(symbol, []) if hasattr(bars, 'data') else []
        if not symbol_bars:
            return None
        volumes = [b.volume for b in symbol_bars if b.volume and b.volume > 0]
        if not volumes:
            return None
        avg_vol = sum(volumes) / len(volumes)
        _avg_daily_volume_cache[symbol] = (today, avg_vol)
        return avg_vol
    except Exception as exc:
        logger.debug("fetch_avg_daily_volume failed for {}: {}", symbol, exc)
        return None


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


def derive_bar_enrichment(
    bars: list[Bar],
) -> tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    """Derive shared enrichment math from a bar window.

    Returns ``(vwap, day_high, prior_hod, dollar_volume_5m)``.
    Shared by live paper-mode and off-hours sim snapshot builders so the
    enrichment math stays identical in both paths.
    """
    total_volume = sum(bar.volume for bar in bars)
    vwap = (
        sum(bar.close * bar.volume for bar in bars) / total_volume
        if total_volume > 0 else None
    )
    day_high = max((bar.high for bar in bars), default=None)
    unique_highs = sorted({bar.high for bar in bars}, reverse=True)
    prior_hod = unique_highs[1] if len(unique_highs) >= 2 else day_high
    # Guard: need at least 5 bars to compute 5-min dollar volume.
    # Fewer bars → None (hard filter skips, doesn't undercount).
    if bars and len(bars) >= 5:
        dollar_volume_5m = sum(bar.close * bar.volume for bar in bars[-5:]) * IEX_SCALE
    else:
        dollar_volume_5m = None
    return vwap, day_high, prior_hod, dollar_volume_5m


# ── Main entry point ────────────────────────────────────────────────────


def _bar_from_alpaca_bar(alpaca_bar) -> Bar:
    return Bar(
        open=alpaca_bar.open,
        high=alpaca_bar.high,
        low=alpaca_bar.low,
        close=alpaca_bar.close,
        volume=alpaca_bar.volume,
        timestamp=getattr(alpaca_bar, "timestamp", None),
    )


def _snapshot_from_alpaca_snapshot(
    candidate,
    alpaca_snapshot,
    *,
    now: datetime,
    initial_bars: Optional[list[Bar]] = None,
    initial_five_min_bars: Optional[list[Bar]] = None,
    api_key: Optional[str] = None,
    secret_key: Optional[str] = None,
) -> Optional[MarketSnapshot]:
    if alpaca_snapshot is None:
        return None

    alpaca_quote = getattr(alpaca_snapshot, "latest_quote", None)

    quote_age_seconds: Optional[float] = None
    spread_pct: Optional[float] = None
    mid_price: Optional[float] = None
    if alpaca_quote is not None and getattr(alpaca_quote, "timestamp", None) is not None:
        age = (now - alpaca_quote.timestamp).total_seconds()
        quote_age_seconds = max(0.0, age)

        bid = alpaca_quote.bid_price
        ask = alpaca_quote.ask_price
        if bid is not None and ask is not None and bid > 0.0 and ask > 0.0 and ask >= bid:
            mid = (bid + ask) / 2.0
            if mid > 0.0:
                spread_pct = (ask - bid) / mid * 100.0
                mid_price = mid

    if mid_price is not None and mid_price > 0:
        candidate = candidate.model_copy(update={"price": mid_price})

    if initial_bars is not None:
        bars = initial_bars
    else:
        minute_bar = getattr(alpaca_snapshot, "minute_bar", None)
        bars = [_bar_from_alpaca_bar(minute_bar)] if minute_bar is not None else []

    close_prices = [b.close for b in bars]
    ema9 = _compute_ema(close_prices, 9) if bars else None
    vwap, day_high, prior_hod, dollar_volume_5m = derive_bar_enrichment(bars)

    daily_bar = getattr(alpaca_snapshot, "daily_bar", None)
    previous_daily_bar = getattr(alpaca_snapshot, "previous_daily_bar", None)
    if daily_bar is not None and getattr(daily_bar, "high", None) is not None:
        day_high = daily_bar.high
    if previous_daily_bar is not None and getattr(previous_daily_bar, "high", None) is not None:
        prior_hod = previous_daily_bar.high
    daily_volume = getattr(daily_bar, "volume", None) if daily_bar else None

    # Compute RVOL: today's cumulative volume / 20-day average daily volume.
    # Activates 25 attention points that were previously dead code.
    rvol = candidate.relative_volume
    if daily_volume and daily_volume > 0 and api_key and secret_key:
        avg_vol = fetch_avg_daily_volume(candidate.symbol, api_key, secret_key)
        if avg_vol and avg_vol > 0:
            rvol = round(daily_volume / avg_vol, 2)

    return MarketSnapshot(
        candidate=candidate,
        bars=bars if bars else None,
        five_min_bars=initial_five_min_bars if initial_five_min_bars else None,
        vwap=vwap,
        ema9=ema9,
        day_high=day_high,
        prior_hod=prior_hod,
        quote_age_seconds=quote_age_seconds,
        spread_pct=spread_pct,
        rvol=rvol,
        dollar_volume_5m=dollar_volume_5m,
        daily_volume=daily_volume,
    )


def build_market_snapshots(
    candidates,
    *,
    api_key: Optional[str] = None,
    secret_key: Optional[str] = None,
) -> dict[str, Optional[MarketSnapshot]]:
    """Fetch Alpaca snapshots for all *candidates* in one batch request."""
    symbols = [c.symbol for c in candidates]
    if not symbols:
        return {}

    try:
        from alpaca.data.historical.stock import StockHistoricalDataClient
        from alpaca.data.requests import StockSnapshotRequest, StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        from alpaca.data.enums import DataFeed
    except ImportError:
        logger.info(
            "alpaca-py not installed — batch market data enrichment unavailable. "
            "Install with: pip install alpaca-py"
        )
        return {symbol: None for symbol in symbols}

    _api_key = api_key or os.getenv("ALPACA_API_KEY")
    _secret_key = secret_key or os.getenv("ALPACA_SECRET_KEY")

    if not _api_key or not _secret_key:
        logger.info(
            "Alpaca API keys not configured — batch market data enrichment unavailable. "
            "Set ALPACA_API_KEY / ALPACA_SECRET_KEY in .env"
        )
        return {symbol: None for symbol in symbols}

    try:
        client = StockHistoricalDataClient(_api_key, _secret_key)

        # ── Snapshots (quote / trade / daily-bar) ────────────
        req = StockSnapshotRequest(symbol_or_symbols=symbols, feed=DataFeed.IEX)
        alpaca_snapshots = client.get_stock_snapshot(req)

        # ── Recent multi-minute bars for entry / ATR ─────────
        # Catch failure independently so snapshot data is not discarded.
        alpaca_bars_by_symbol: dict = {}
        alpaca_five_min_bars_by_symbol: dict = {}
        try:
            bars_req = StockBarsRequest(
                symbol_or_symbols=symbols,
                timeframe=TimeFrame.Minute,
                limit=BAR_LIMIT,
                feed=DataFeed.IEX,
            )
            bar_set = client.get_stock_bars(bars_req)
            alpaca_bars_by_symbol = bar_set.data
        except Exception as exc:
            logger.warning(
                "Batch bars fetch failed (snapshots preserved): {}", exc,
            )
        try:
            five_min_bars_req = StockBarsRequest(
                symbol_or_symbols=symbols,
                timeframe=TimeFrame(amount=5, unit=TimeFrameUnit.Minute),
                limit=BAR_LIMIT,
                feed=DataFeed.IEX,
            )
            five_min_bar_set = client.get_stock_bars(five_min_bars_req)
            alpaca_five_min_bars_by_symbol = five_min_bar_set.data
        except Exception as exc:
            logger.warning(
                "Batch 5-min bars fetch failed (snapshots preserved): {}", exc,
            )

        now = datetime.now(timezone.utc)
        return {
            c.symbol: _snapshot_from_alpaca_snapshot(
                c,
                alpaca_snapshots.get(c.symbol),
                now=now,
                initial_bars=[
                    _bar_from_alpaca_bar(b)
                    for b in alpaca_bars_by_symbol.get(c.symbol, [])
                ] if alpaca_bars_by_symbol.get(c.symbol) else None,
                initial_five_min_bars=[
                    _bar_from_alpaca_bar(b)
                    for b in alpaca_five_min_bars_by_symbol.get(c.symbol, [])
                ] if alpaca_five_min_bars_by_symbol.get(c.symbol) else None,
                api_key=_api_key,
                secret_key=_secret_key,
            )
            for c in candidates
        }
    except Exception as exc:
        logger.warning("Batch market-data enrichment failed: {}", exc)
        return {symbol: None for symbol in symbols}


def build_market_snapshot(
    candidate,
    *,
    api_key: Optional[str] = None,
    secret_key: Optional[str] = None,
) -> Optional[MarketSnapshot]:
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
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        from alpaca.data.enums import DataFeed
    except ImportError:
        logger.info(
            "alpaca-py not installed — market data enrichment unavailable. "
            "Install with: pip install alpaca-py"
        )
        return None

    _api_key = api_key or os.getenv("ALPACA_API_KEY")
    _secret_key = secret_key or os.getenv("ALPACA_SECRET_KEY")

    if not _api_key or not _secret_key:
        logger.info(
            "Alpaca API keys not configured — market data enrichment unavailable. "
            "Set ALPACA_API_KEY / ALPACA_SECRET_KEY in .env"
        )
        return None

    try:
        client = StockHistoricalDataClient(_api_key, _secret_key)
        now = datetime.now(timezone.utc)

        # ── Latest quote ──────────────────────────────────────────
        quote_req = StockLatestQuoteRequest(
            symbol_or_symbols=candidate.symbol, feed=DataFeed.IEX,
        )
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
            limit=BAR_LIMIT,
            feed=DataFeed.IEX,
        )
        bar_set = client.get_stock_bars(bars_req)
        alpaca_bars = bar_set.data.get(candidate.symbol, [])

        try:
            five_min_bars_req = StockBarsRequest(
                symbol_or_symbols=candidate.symbol,
                timeframe=TimeFrame(amount=5, unit=TimeFrameUnit.Minute),
                limit=BAR_LIMIT,
                feed=DataFeed.IEX,
            )
            five_min_bar_set = client.get_stock_bars(five_min_bars_req)
            alpaca_five_min_bars = five_min_bar_set.data.get(candidate.symbol, [])
        except Exception as exc:
            logger.warning(
                "5-min bars fetch failed for {} (snapshot preserved): {}",
                candidate.symbol,
                exc,
            )
            alpaca_five_min_bars = []

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

        five_min_bars: list[Bar] = []
        for ab in alpaca_five_min_bars:
            five_min_bars.append(Bar(
                open=ab.open,
                high=ab.high,
                low=ab.low,
                close=ab.close,
                volume=ab.volume,
                timestamp=ab.timestamp,
            ))

        # ── Derived enrichment ────────────────────────────────────
        close_prices = [b.close for b in bars]
        ema9 = _compute_ema(close_prices, 9) if bars else None
        vwap, day_high, prior_hod, dollar_volume_5m = derive_bar_enrichment(bars)

        # ── Daily volume + RVOL ───────────────────────────────────
        # Fetch latest daily bar for cumulative volume. Separate call
        # because StockLatestQuoteRequest doesn't return daily_bar.
        daily_volume: Optional[float] = None
        try:
            from alpaca.data.requests import StockLatestBarRequest
            latest_bar_req = StockLatestBarRequest(
                symbol_or_symbols=candidate.symbol, feed=DataFeed.IEX,
            )
            latest_bars = client.get_stock_latest_bar(latest_bar_req)
            latest_bar = latest_bars.get(candidate.symbol) if latest_bars else None
            if latest_bar:
                daily_volume = getattr(latest_bar, "volume", None)
        except Exception as exc:
            logger.debug("Latest bar fetch failed for {}: {}", candidate.symbol, exc)

        rvol = candidate.relative_volume
        if daily_volume and daily_volume > 0:
            avg_vol = fetch_avg_daily_volume(candidate.symbol, _api_key, _secret_key)
            if avg_vol and avg_vol > 0:
                rvol = round(daily_volume / avg_vol, 2)
            else:
                logger.debug("RVOL fallback to scanner value for {}", candidate.symbol)

        return MarketSnapshot(
            candidate=candidate,
            bars=bars if bars else None,
            five_min_bars=five_min_bars if five_min_bars else None,
            vwap=vwap,
            ema9=ema9,
            day_high=day_high,
            prior_hod=prior_hod,
            quote_age_seconds=quote_age_seconds,
            spread_pct=spread_pct,
            rvol=rvol,
            dollar_volume_5m=dollar_volume_5m,
            daily_volume=daily_volume,
        )

    except Exception as exc:
        logger.warning(
            "Market-data enrichment failed for {}: {}",
            candidate.symbol,
            exc,
        )
        return None
